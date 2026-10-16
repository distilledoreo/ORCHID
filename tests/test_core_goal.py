from __future__ import annotations

import json
from pathlib import Path

from memory_gateway.benchmark_manifest import (
    pareto_manifest_extensions,
    validate_manifest_for_run,
)
from memory_gateway.core_goal import (
    CORE_CANONICALIZER_ENABLED,
    DEFAULT_MANIFEST_PATH,
    FOREGROUND_EMERGENCY_CEILING_TOKENS,
    FOREGROUND_TARGET_TOKENS,
    core_goal_manifest_document,
    core_goal_status,
    verify_core_goal_manifest,
    write_core_goal_manifest,
)


def test_checked_in_core_goal_manifest_matches_current_contract() -> None:
    assert DEFAULT_MANIFEST_PATH.exists()
    result = verify_core_goal_manifest()
    assert result["ok"], result["reasons"]
    document = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert document == core_goal_manifest_document()
    assert document["context"] == {
        "foreground_target_tokens": FOREGROUND_TARGET_TOKENS,
        "foreground_emergency_ceiling_tokens": (
            FOREGROUND_EMERGENCY_CEILING_TOKENS
        ),
        "minimum_history_multiple": 5,
    }


def test_manifest_seal_is_deterministic_and_tampering_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "CORE_GOAL_MANIFEST.json"
    first = write_core_goal_manifest(path)
    second = write_core_goal_manifest(path)
    assert first == second
    assert verify_core_goal_manifest(path)["ok"] is True

    document = json.loads(path.read_text(encoding="utf-8"))
    document["context"]["foreground_target_tokens"] = 99_999
    path.write_text(json.dumps(document), encoding="utf-8")
    result = verify_core_goal_manifest(path)
    assert result["ok"] is False
    assert result["checks"]["self_hash"] is False
    assert result["checks"]["sealed"] is False


def test_core_goal_status_passes_unpaid_frozen_foundations() -> None:
    report = core_goal_status()
    assert report["core_contract_ready"] is True, report["blockers"]
    assert report["paid_benchmark_ready"] is False
    assert report["organizer"] == {
        "raw_events": 199,
        "source_items": 216,
        "selector_chunks": 63,
        "exact_once": True,
    }
    assert report["canonicalizer_verdict"] == "KEEP"
    assert report["checks"]["foreground_target_runtime_default"] is True
    assert report["checks"]["foreground_ceiling_runtime_default"] is True
    assert report["next_unpaid_steps"]


def test_pareto_manifest_uses_core_context_and_canonicalizer_policy() -> None:
    pareto = pareto_manifest_extensions()
    assert pareto["context_limits"]["foreground_target_tokens"] == (
        FOREGROUND_TARGET_TOKENS
    )
    assert pareto["context_limits"]["foreground_hard_limit_tokens"] == (
        FOREGROUND_EMERGENCY_CEILING_TOKENS
    )
    assert pareto["canonicalizer_policy"] == {
        "enabled": CORE_CANONICALIZER_ENABLED,
        "frozen_gate_verdict": "KEEP",
    }


def test_run_validation_rejects_bypassing_kept_canonicalizer() -> None:
    manifest = {
        "pareto": pareto_manifest_extensions(),
        "run_policy": {"manual_rescue": "forbidden"},
    }
    orchid_config = {
        "foreground_model": "agnes-2.5-flash",
        "canonicalizer_enabled": False,
        "selector_chunk_target_tokens": 1_200,
    }
    result = validate_manifest_for_run(manifest, orchid_config=orchid_config)
    assert result["all_ok"] is False
    assert result["checks"]["canonicalizer_policy_aligned"] is False
    assert any("frozen KEEP" in reason for reason in result["reasons"])

    orchid_config["canonicalizer_enabled"] = True
    result = validate_manifest_for_run(manifest, orchid_config=orchid_config)
    assert result["checks"]["canonicalizer_policy_aligned"] is True
