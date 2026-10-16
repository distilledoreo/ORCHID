from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_report_uses_final_checkpoint_when_clean_gateway_shutdown_loses_snapshot(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "preflight"
    artifact.mkdir()
    _write_json(
        artifact / "driver_summary.json",
        {
            "reason": "preflight_gate_satisfied",
            "post_promotion_turns": 5,
            "rpc_log": [
                {"type": "prompt", "id": "preflight-continue-1"},
            ],
            "errors": [],
            "orchid": {"error": "fetch failed"},
        },
    )
    _write_json(
        artifact / "controller" / "checkpoint-1.json",
        {
            "reason": "preflight_gate_satisfied",
            "post_promotion_turns": 5,
            "orchid": {
                "thread": {
                    "jobs": [
                        {"id": f"job-{index}", "status": "PROMOTED"}
                        for index in range(3)
                    ]
                }
            },
        },
    )
    (artifact / "telemetry.jsonl").write_text(
        json.dumps(
            {
                "request_id": "request-1",
                "active": True,
                "active_tokens": 100,
                "inbound_estimated_tokens": 1_000,
                "assembled_estimated_tokens": 500,
                "provider_input_tokens": 520,
                "estimator_vs_provider_ratio": 0.96,
                "non_protocol_covered_raw_tokens_forwarded": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "tools" / "orchid_foreground_preflight_report.py"),
            str(artifact),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((artifact / "SUMMARY.json").read_text(encoding="utf-8"))
    assert summary["status"] == "PASS"
    assert summary["promotions"] == 3
    assert summary["post_promotion_turns"] == 5
