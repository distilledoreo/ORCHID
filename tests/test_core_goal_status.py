from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_core_goal_status_command_is_unpaid_and_ready() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/core_goal_status.py"),
            "check",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert payload["core_contract_ready"] is True
    assert payload["paid_benchmark_ready"] is False
    assert payload["checks"]["organizer_exact_once"] is True
    assert payload["organizer"]["raw_events"] == 199
    assert payload["next_unpaid_steps"]


def test_core_goal_status_command_fails_closed_for_tampered_manifest(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/core_goal_status.py"),
            "check",
            "--manifest",
            str(manifest),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["core_contract_ready"] is False
    assert payload["checks"]["manifest_sealed"] is False
    assert "manifest_sealed" in payload["blockers"]
