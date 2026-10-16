from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from configure_solar_pro4_provider import render_env  # noqa: E402


def test_render_env_uses_pareto_reference_defaults() -> None:
    content = render_env(api_key="sk-or-v1-test")
    assert "BENCHMARK_MODEL_ID='upstage/solar-pro4'" in content
    assert "BENCHMARK_CONTEXT_TOKENS=128000" in content
    assert "BENCHMARK_PROVIDER_BASE_URL='https://openrouter.ai/api/v1'" in content
    assert "BENCHMARK_INPUT_USD_PER_MILLION=0.03" in content
    assert "BENCHMARK_OUTPUT_USD_PER_MILLION=0.12" in content


def test_render_env_shell_quotes_api_key(tmp_path: Path) -> None:
    content = render_env(api_key="sk-or-v1-abc'def")
    assert "BENCHMARK_PROVIDER_API_KEY='sk-or-v1-abc'\"'\"'def'" in content


def test_resolve_api_key_rejects_openai_style_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("BENCHMARK_PROVIDER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openai-style")
    try:
        from configure_solar_pro4_provider import resolve_api_key

        resolve_api_key()
        raised = False
    except SystemExit as error:
        raised = True
        assert "sk-or-" in str(error)
        assert "setup-solar-pro4" in str(error)
    assert raised


def test_resolve_api_key_reads_first_line_from_file(tmp_path: Path) -> None:
    key_file = tmp_path / "openrouter.key"
    key_file.write_text("sk-or-v1-from-file\n", encoding="utf-8")
    from configure_solar_pro4_provider import resolve_api_key

    assert resolve_api_key(key_file=key_file) == "sk-or-v1-from-file"
