import pytest

from memory_gateway.evaluation import evaluate


def test_real_gateway_replay_baselines():
    report = evaluate()
    assert report["mode"] == "deterministic-fakes"
    assert len(report["fixture_hash"]) == 64
    full, rolling, legacy = report["arms"]
    assert full["calls"] == 0
    assert rolling["calls"] == rolling["promotions"] == 10
    assert legacy["calls"] >= 30
    for arm in report["arms"]:
        assert all(probe["passed"] for probe in arm["probes"].values())
        assert arm["per_1000_source_tokens"]["input_tokens"] == pytest.approx(arm["input_tokens"] * 1000 / arm["source_tokens"])
