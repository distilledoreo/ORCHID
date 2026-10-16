import math
from types import SimpleNamespace

import pytest

from experiments.strata_specprefill.core import (
    compatible_ids,
    importance,
    select_positions,
    token_hash,
    validate_input,
)


def test_token_compatibility_requires_same_text_and_id_including_specials():
    descriptor = {
        "model": {"vocab": {"a": 1, "b": 2}},
        "added_tokens": [{"content": "<end>", "id": 3}],
    }
    assert compatible_ids({"a": 1, "b": 8, "<end>": 3, "extra": 4}, descriptor) == {1, 3}


@pytest.mark.parametrize("ids", [[], [True], [3], [-1], [1.0]])
def test_refuse_unknown_or_noninteger_prompt_ids(ids):
    with pytest.raises(ValueError):
        validate_input(ids, {1, 2})


def test_input_identity_is_order_sensitive_and_validated():
    validate_input([1, 2, 1], {1, 2})
    assert token_hash([1, 2]) != token_hash([2, 1])


def test_runner_sends_original_ids_and_includes_preparation_in_ttft(monkeypatch):
    from experiments.strata_specprefill import run

    times = iter([100.0, 101.0, 103.0])
    monkeypatch.setattr(run.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(run, "prepare", lambda *_: [10, 20, 30])
    received = []

    def post(url, body):
        received.append(body)
        return {
            "token_ids": [99],
            "timings": {"ttft_s": 0.5, "first_token_monotonic_s": 102.0},
            "prefill": {
                "token_ids": [10, 30],
                "position_ids": [0, 2],
                "next_position": 3,
                "device_table_verified": True,
            },
        }

    calls = []

    def trace(ids, ratio):
        calls.append((ids, ratio))
        return {"positions": [0, 2]}

    monkeypatch.setattr(run, "post", post)
    case = {"id": "test", "input_ids": [10, 20, 30], "answer": "KELP47"}
    result = run.run_arm(
        SimpleNamespace(url="unused"),
        case,
        "keep50",
        "test",
        SimpleNamespace(decode=lambda _: "KELP47"),
        None,
        SimpleNamespace(trace=trace),
    )
    assert calls == [([10, 20, 30], 0.5)]
    assert received[0]["token_ids"] == [10, 30]
    assert received[0]["position_ids"] == [0, 2]
    assert received[0]["original_prompt_length"] == 3
    assert result["pipeline_ttft_s"] == 2.0
    assert result["pre_request_s"] == 1.0
    assert result["correct"]


@pytest.fixture
def torch():
    return pytest.importorskip("torch")


def test_pool_precedes_max_over_layers_and_heads(torch):
    probabilities = torch.tensor([[[[0.8, 0.1, 0.1]]], [[[0.1, 0.1, 0.8]]]])
    scores = importance(probabilities.log(), pool_kernel=3)
    assert scores.tolist() == pytest.approx([0.3, 1 / 3, 0.3])
    # Max-before-pool would give 1.7/3 for the middle token.
    assert not math.isclose(scores[1].item(), 1.7 / 3)


def test_query_rows_are_averaged_after_head_max(torch):
    p = torch.tensor([[[[0.8, 0.2], [0.1, 0.9]]]])
    assert importance(p.log(), pool_kernel=1).tolist() == pytest.approx([0.45, 0.55])


def test_chunk_means_ceil_and_partial_last_chunk(torch):
    scores = torch.tensor([0.0] * 32 + [1.0] * 32 + [2.0] * 6)
    positions = select_positions(scores, 0.5)
    assert positions == list(range(32, 70))
    assert select_positions(scores, 1) == list(range(70))


def test_chunk_selection_uses_mean_not_sum(torch):
    scores = torch.tensor([1.0] * 32 + [0.0] * 32 + [2.0])
    assert select_positions(scores, 0.1) == [64]


def test_invalid_scoring_configuration_is_rejected(torch):
    with pytest.raises(ValueError):
        importance(torch.zeros(2, 3), pool_kernel=3)
    with pytest.raises(ValueError):
        importance(torch.zeros(1, 1, 1, 3), pool_kernel=2)
    with pytest.raises(ValueError):
        select_positions(torch.tensor([float("nan")]), 0.5)
    with pytest.raises(ValueError):
        select_positions(torch.ones(3), 0)
