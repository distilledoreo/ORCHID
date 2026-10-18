"""Stateful cache tests, including the hybrid-state lookahead isolation contract."""
from types import SimpleNamespace

import pytest


def fake_model(monkeypatch, capture=True):
    torch = pytest.importorskip('torch')
    pytest.importorskip('transformers')
    from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS

    monkeypatch.setitem(ALL_ATTENTION_FUNCTIONS._global_mapping, 'sdpa', lambda *a, **k: None)

    def model(input_ids, past_key_values=None, **kwargs):
        cache = {'ids': []} if past_key_values is None else past_key_values
        new = input_ids[0].tolist()
        cache['ids'].extend(new)  # Mutable recurrent state: cropping just KV cannot undo this.
        if capture:
            key = torch.tensor(cache['ids'], dtype=torch.float32).reshape(1, 1, -1, 1)
            query = torch.tensor(new, dtype=torch.float32).reshape(1, 1, -1, 1)
            ALL_ATTENTION_FUNCTIONS['sdpa'](SimpleNamespace(layer_idx=3), query, key, key, None)
        return SimpleNamespace(past_key_values=cache, logits=torch.tensor([[[0., 1., 0.]]]))
    return torch, model


def test_cached_prefix_reuses_only_exact_prompt_and_isolates_draft_mutation(monkeypatch):
    torch, model = fake_model(monkeypatch)
    from experiments.strata_specprefill.chunked_student import trace_chunked
    state = {}

    def trace(ids, cache):
        return trace_chunked(model, torch, set(), ids, .3, prefill_block=2,
                             lookahead=3, pool_kernel=1, device='cpu', prefix_state=cache)
    trace([2, 3, 4], state)
    assert state['cache']['ids'] == [2, 3, 4]  # Draft IDs are absent.
    cached = trace([2, 3, 4, 5, 6], state)
    full = trace([2, 3, 4, 5, 6], None)
    assert cached['reused_prefix_tokens'] == 3
    assert cached['prefill_tokens'] == 2
    assert cached['scores'] == full['scores']
    assert cached['positions'] == full['positions']
    assert cached['draft_sampled_ids'] == full['draft_sampled_ids']
    assert state['cache']['ids'] == [2, 3, 4, 5, 6]
    divergent = trace([2, 8, 4, 5, 6, 7], state)
    assert divergent['reused_prefix_tokens'] == 0
    assert state['cache']['ids'] == [2, 8, 4, 5, 6, 7]


def test_missing_final_query_fails_without_replaying_or_leaving_authoritative_cache(monkeypatch):
    torch, model = fake_model(monkeypatch, capture=False)
    from experiments.strata_specprefill.chunked_student import trace_chunked
    state = {'ids': [2], 'cache': {'ids': [2]}}
    with pytest.raises(RuntimeError, match='replay is unsafe'):
        trace_chunked(model, torch, set(), [2, 3, 4], .3, prefill_block=2,
                      device='cpu', prefix_state=state)
    assert state == {}
