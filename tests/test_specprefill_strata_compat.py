"""The gateway in Strata's place: a client written for Strata's own server finds the
endpoints, model names and request fields it uses."""
import pytest
from fastapi.testclient import TestClient
from test_specprefill_gateway_auto import TOOL, chat, service

from memory_gateway.config import RuntimeConfig
from memory_gateway.gateway import create_app


def app_for(tmp_path, **options):
    s, r = service(tmp_path, **options)
    return create_app(config=RuntimeConfig.from_env(db_path=str(tmp_path / 'g.db')), specprefill_service=s), s, r


def test_health_and_status_answer_in_stratas_format(tmp_path):
    app, s, _ = app_for(tmp_path, elastic_window=262144)
    with TestClient(app) as client:
        s.warming = True
        assert client.get('/health').json()['loaded'] is False  # Loading: a Strata client keeps waiting.
        s.warming = False
        s._resources()
        health = client.get('/health').json()
        assert health['status'] == 'ok' and health['loaded'] is True and health['service'] == 'strata'
        assert health['model'] == s.options.model
        first, second = client.get('/v1/status').json(), client.get('/v1/status').json()
        assert first['loaded'] is True and first['context']['native'] == first['cache_max_tokens']
        # A client tells a changed server by these three: stable while the process runs.
        assert [first[k] for k in ('started', 'engine', 'model')] == [second[k] for k in ('started', 'engine', 'model')]
    s.close()


def test_aliases_and_any_name_are_accepted_as_strata_accepts_them(tmp_path):
    s, _ = service(tmp_path, model_aliases=('qwen3.8-flash-next',))
    s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), model='qwen3.8-flash-next'))
    with pytest.raises(ValueError, match='Unknown model'):
        s.complete(None, None, None, chat(('user', 'Other. ' + TOOL + '\n\nReturn KEYB'), model='another-model'))
    s.close()
    s, _ = service(tmp_path / 'any', model_aliases=('*',))
    s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), model='whatever-the-client-names'))
    s.close()


def test_stratas_own_request_fields_reach_native_and_do_not_divert_lean(tmp_path):
    s, r = service(tmp_path)
    lean = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), cache_prompt=True))
    assert lean['orchid']['engine'] == 'lean'  # Not a reason to go native.
    s.complete(None, None, None, chat(('user', 'x'), logprobs=True, strata_prefix={'messages': 1}, cache_prompt=True))
    assert r.native_calls[-1]['strata_prefix'] == {'messages': 1} and r.native_calls[-1]['cache_prompt'] is True
    s.close()
