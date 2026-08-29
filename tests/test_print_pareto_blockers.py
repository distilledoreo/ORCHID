from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_print_pareto_blockers_json_reports_blockers(tmp_path: Path) -> None:
    (tmp_path / "artifacts/selector_contract").mkdir(parents=True)
    script = ROOT / "tools/print_pareto_blockers.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(tmp_path),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"BENCHMARK_PROVIDER_API_KEY": "sk-openai-style"},
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["ready"] is False
    assert payload["checks"]["provider_profile"] is False
