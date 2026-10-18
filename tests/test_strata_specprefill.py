import hashlib
import json
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


@pytest.mark.parametrize("arm,ratio", [("keep50", 0.5), ("keep30", 0.3), ("keep10", 0.1)])
def test_runner_sends_original_ids_and_includes_preparation_in_ttft(monkeypatch, arm, ratio):
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
        arm,
        "test",
        SimpleNamespace(decode=lambda _: "KELP47"),
        None,
        SimpleNamespace(trace=trace),
    )
    assert calls == [([10, 20, 30], ratio)]
    assert received[0]["token_ids"] == [10, 30]
    assert received[0]["position_ids"] == [0, 2]
    assert received[0]["original_prompt_length"] == 3
    assert result["pipeline_ttft_s"] == 2.0
    assert result["pre_request_s"] == 1.0
    assert result["correct"]


@pytest.mark.parametrize("ratio", [0.1, 0.3, 0.5, 1.0])
def test_keep_rate_arm_roundtrip(ratio):
    from experiments.strata_specprefill.run import arm_ratio, keep_arm

    assert arm_ratio(keep_arm(ratio)) == ratio


@pytest.mark.parametrize("ratio", [0, -0.1, 1.1, float("nan"), float("inf")])
def test_invalid_keep_rates_are_rejected(ratio):
    from experiments.strata_specprefill.run import keep_arm

    with pytest.raises(ValueError):
        keep_arm(ratio)


@pytest.mark.parametrize("extended", [False, True])
def test_report_counts_frozen_arms_and_refuses_incomplete_runs(tmp_path, extended):
    from experiments.strata_specprefill.report import summarize

    arms = ["full", "keep100", "keep50"] + (["keep30", "keep10"] if extended else [])
    protocol = {"cases": [{"id": str(i), "input_ids": [10, 20]} for i in range(4)]}
    if extended:
        protocol["arms"] = arms
    digest = hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()
    protocol["sha256"] = digest
    (tmp_path / "protocol.json").write_text(json.dumps(protocol))
    rows = []
    for case in protocol["cases"]:
        for epoch, arm in [("standalone", "full"), *[("colocated", a) for a in arms]]:
            rows.append(
                {
                    "case": case["id"],
                    "epoch": epoch,
                    "arm": arm,
                    "correct": True,
                    "pipeline_ttft_s": 1.0,
                    "server_ttft_s": 0.8,
                    "pre_request_s": 0.1,
                    "total_s": 2,
                    "retained_tokens": 2,
                    "original_tokens": 2,
                    "output_ids": [99],
                    "trace": None
                    if arm == "full"
                    else {
                        "student_selection_s": 0.1,
                        "query_rows": 2,
                        "attention_layer_ids": [3],
                        "peak_student_allocator_mib": 1,
                    },
                }
            )
    requests = tmp_path / "requests.jsonl"
    requests.write_text("\n".join(json.dumps(row) for row in rows))
    (tmp_path / "COMPLETE.json").write_text(
        json.dumps({"requests": len(rows), "protocol_sha256": digest})
    )
    summary = summarize(tmp_path)
    assert summary["measured_requests"] == (24 if extended else 16)
    assert len(summary["variants"]) == len(arms) + 1
    assert summary["warmup_requests_excluded"] == len(arms) + 1
    if extended:
        assert set(summary["per_case"][0]["selections"]) == {
            "keep100",
            "keep50",
            "keep30",
            "keep10",
        }
    requests.write_text("\n".join(json.dumps(row) for row in rows[:-1]))
    with pytest.raises(ValueError, match="Incomplete"):
        summarize(tmp_path)


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


def test_frozen_selection_rejects_prompt_drift_and_invalid_positions():
    from experiments.strata_specprefill.core import validate_frozen_selection
    trace = {'input_hash': token_hash([1, 2, 3]), 'input_tokens': 3,
             'per_rate': {'0.3': {'positions': [0, 2], 'retained_tokens': 2}}}
    validate_frozen_selection([1, 2, 3], trace)
    with pytest.raises(ValueError, match='exact prompt'):
        validate_frozen_selection([1, 9, 3], trace)
    for positions in ([2, 0], [0, 0], [True], [3], []):
        trace['per_rate']['0.3']['positions'] = positions
        with pytest.raises(ValueError, match='positions'):
            validate_frozen_selection([1, 2, 3], trace)


def test_selected_response_checks_ids_not_just_positions():
    from experiments.strata_specprefill.core import validate_selected_response
    response = {'prefill': {'token_ids': [1, 3], 'position_ids': [0, 2],
                           'next_position': 3, 'original_prompt_length': 3,
                           'device_table_verified': True}}
    validate_selected_response([1, 2, 3], [0, 2], response)
    response['prefill']['token_ids'] = [1, 9]
    with pytest.raises(RuntimeError, match='exact selected IDs'):
        validate_selected_response([1, 2, 3], [0, 2], response)


def test_stable_selection_preserves_order_and_invalidates_backfilled_suffix():
    from experiments.strata_specprefill.stable_selection import StableSelection
    policy = StableSelection()
    assert policy.plan([10, 11, 12, 13], [0, 2])[0] == [0, 2]
    pos, audit = policy.plan([10, 11, 12, 13, 14, 15], [0, 4, 5])
    assert pos == [0, 2, 4, 5]
    assert audit['prefix_cells_unchanged'] == 2
    assert audit['backfilled_tokens'] == 0
    pos, audit = policy.plan([10, 11, 12, 13, 14, 15, 16], [1, 6])
    assert pos == [0, 1, 2, 4, 5, 6]  # Never append old position 1 after position 5.
    assert audit['prefix_cells_unchanged'] == 1
    assert audit['backfilled_tokens'] == 1
    pos, audit = policy.plan([99, 11, 12], [2])
    assert pos == [2]
    assert audit['history_reset']


def test_cache_report_refuses_duplicate_missing_or_changed_input_rows():
    from experiments.strata_specprefill.cache_report import validate_records
    frozen = {('session', 0): {'input_ids': [1, 2, 3]}}
    record = {'session': 'session', 'turn': 0, 'arm': 'cache',
              'input_hash': token_hash([1, 2, 3])}
    validate_records([record], ['cache'], frozen)
    for records in ([], [record, record], [{**record, 'input_hash': token_hash([1, 9, 3])}]):
        with pytest.raises(ValueError):
            validate_records(records, ['cache'], frozen)
