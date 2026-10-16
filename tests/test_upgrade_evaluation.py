import pytest

from memory_gateway.evaluation import evaluate


def test_real_gateway_replay_baselines():
    report = evaluate()
    assert report["mode"] == "deterministic-fakes"
    assert len(report["fixture_hash"]) == 64
    full, rolling, legacy = report["arms"][:3]
    assert full["calls"] == 0
    assert rolling["calls"] == rolling["promotions"] == 10
    assert legacy["calls"] >= 30
    observer = report["arms"][3]
    assert observer["calls"] == observer["promotions"] == 10
    assert report["legacy_to_observer_input_ratio"] >= 5
    assert observer["raw_recall_probe"]["passed"]
    for arm in report["arms"]:
        assert all(probe["passed"] for probe in arm["probes"].values())
        assert arm["per_1000_source_tokens"]["input_tokens"] == pytest.approx(arm["input_tokens"] * 1000 / arm["source_tokens"])


def test_runtime_imports_use_only_this_checkout():
    import sys
    from pathlib import Path
    import memory_gateway.gateway
    assert callable(memory_gateway.gateway.create_app)
    root = Path(__file__).resolve().parents[1]
    external = [name for name, module in sys.modules.items() if name.startswith("memory_gateway")
                and getattr(module, "__file__", None) and not Path(module.__file__).resolve().is_relative_to(root)]
    assert not external
