from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from configure_agnes_provider import render_env  # noqa: E402


def test_render_env_uses_agnes_reference_defaults() -> None:
    content = render_env(api_key="sk-test")
    assert "BENCHMARK_MODEL_ID='agnes-2.5-flash'" in content
    assert "BENCHMARK_CONTEXT_TOKENS=128000" in content
    assert "BENCHMARK_PROVIDER_BASE_URL='https://apihub.agnes-ai.com/v1'" in content
