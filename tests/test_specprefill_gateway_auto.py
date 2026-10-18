"""Lean gateway as a default: automatic threads, real-client request shapes, native fallback."""
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_spec_session import Student, Target, Tok, doc

from memory_gateway.config import RuntimeConfig
from memory_gateway.gateway import create_app
from memory_gateway.specprefill.service import GatewayOptions, SpecPrefillService
from memory_gateway.specprefill.spec_session import SpecSession


class RecordingTarget(Target):
    """Records the per-request sampling and output limit the service sets."""

    def __init__(self):
        super().__init__()
        self.seen, self.maxima, self.fail = [], [], False
        self.sampling = self.on_token = None

    def __call__(self, ids, maximum):
        if self.fail:
            raise RuntimeError('engine died')
        self.seen.append(self.sampling)
        self.maxima.append(maximum)
        output = super().__call__(ids, maximum)
        for token in output:
            if self.on_token:
                self.on_token(token)
        return output


class Resources:
    def __init__(self):
        self.tok, self.student, self.target = Tok(), Student(), RecordingTarget()
        self.selector = self.student
        self.native_calls = []

    def session(self, path, system, limits):
        if path.exists():
            return SpecSession.restore(path, self.tok, self.student, self.target)
        return SpecSession(self.tok, self.student, self.target, system=system, limits=limits, checkpoint=path)

    def native(self, payload, on_text=None):
        self.native_calls.append(payload)
        if on_text:
            on_text('native answer')
        return {'id': 'chatcmpl-native', 'object': 'chat.completion', 'created': 0, 'model': 'm',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'native answer'},
                             'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}}

    def close(self):
        pass


def service(tmp_path, **kwargs):
    base = GatewayOptions(str(tmp_path / 'native'), 'unused', 'unused', engine='lean',
                          context_tokens=60000, student_context_tokens=60000, answer_tokens=32,
                          new_rate=0.05, recall_tokens=200, query_split_tokens=1024, query_tail_tokens=200, native_below_tokens=0)
    resources = Resources()
    return SpecPrefillService(replace(base, **kwargs), resources_factory=lambda: resources), resources


TOOL = doc({'KEYA': '11111', 'KEYB': '22222', 'KEYC': '33333'})


def chat(*turns, **extra):
    messages = [{'role': 'system', 'content': 'Use excerpts.'}]
    messages += [{'role': role, 'content': content} for role, content in turns]
    return {'messages': messages, **extra}


def reply(result):
    return result['choices'][0]['message']['content']


def test_threads_are_found_from_history_without_headers(tmp_path):
    s, r = service(tmp_path)
    first = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    assert reply(first) == '11111'
    second = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), ('assistant', reply(first)),
                                               ('user', 'Return KEYB')))
    assert reply(second) == '22222'
    assert second['orchid']['thread'] == first['orchid']['thread']
    assert len(s.sessions) == 1 and next(iter(s.sessions.values())).turn == 2
    s.close()


def test_retry_without_request_id_replays_and_does_not_call_the_model(tmp_path):
    s, r = service(tmp_path)
    request = chat(('user', TOOL + '\n\nReturn KEYA'))
    first = s.complete(None, None, None, request)
    calls = len(r.target.calls)
    assert s.complete(None, None, None, request) == first
    assert len(r.target.calls) == calls
    s.close()


def test_fork_and_edited_history_become_new_threads(tmp_path):
    s, _ = service(tmp_path)
    first = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    base = [('user', TOOL + '\n\nReturn KEYA'), ('assistant', reply(first))]
    a = s.complete(None, None, None, chat(*base, ('user', 'Return KEYB')))
    b = s.complete(None, None, None, chat(*base, ('user', 'Return KEYC')))  # A fork after turn 1.
    assert (reply(a), reply(b)) == ('22222', '33333')
    assert a['orchid']['thread'] == first['orchid']['thread'] != b['orchid']['thread']
    edited = s.complete(None, None, None, chat(('user', doc({'KEYA': '44444'}) + '\n\nReturn KEYA'),
                                               ('assistant', reply(first)), ('user', 'Return KEYA')))
    assert reply(edited) == '44444'  # The edited earlier message wins: a fresh thread.
    assert edited['orchid']['thread'] not in (a['orchid']['thread'], b['orchid']['thread'])
    s.close()


def test_reserialized_replies_still_continue_the_thread(tmp_path):
    s, _ = service(tmp_path)
    first = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    history = [{'role': 'system', 'content': 'Use excerpts.'},
               {'role': 'user', 'content': [{'type': 'text', 'text': TOOL + '\n\nReturn KEYA'}]},
               {'role': 'assistant', 'content': ' ' + reply(first) + '\n', 'refusal': None,
                'reasoning_content': 'thought about it'},
               {'role': 'user', 'content': 'Return KEYB', 'name': 'me'}]
    second = s.complete(None, None, None, {'messages': history})
    assert reply(second) == '22222' and second['orchid']['thread'] == first['orchid']['thread']
    s.close()


def test_developer_role_and_text_parts_are_served_lean(tmp_path):
    s, r = service(tmp_path)
    result = s.complete(None, None, None, {'messages': [
        {'role': 'developer', 'content': 'Use excerpts.'},
        {'role': 'user', 'content': [{'type': 'text', 'text': TOOL}, {'type': 'text', 'text': '\n\nReturn KEYC'}]}]})
    assert reply(result) == '33333' and result['orchid']['engine'] == 'lean'
    assert not r.native_calls
    s.close()


@pytest.mark.parametrize('extra, reason', [
    ({'tool_choice': 'required', 'tools': [{'type': 'function', 'function': {'name': 'f'}}]}, 'tool_choice required'),
    ({'logprobs': True}, 'field logprobs'),
    ({'response_format': {'type': 'json_object'}}, 'response_format'),
])
def test_requests_lean_cannot_serve_go_native(tmp_path, extra, reason):
    s, r = service(tmp_path)
    result = s.complete(None, None, None, chat(('user', 'Return KEYA'), **extra))
    assert reply(result) == 'native answer'
    assert result['orchid'] == {'engine': 'native', 'reason': reason, 'thread': result['orchid']['thread']}
    assert len(r.native_calls) == 1 and not r.target.calls
    s.close()


def test_an_image_mid_thread_goes_native_and_the_next_lean_turn_catches_up(tmp_path):
    s, r = service(tmp_path)
    first = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    picture = {'role': 'user', 'content': [{'type': 'text', 'text': 'What is this?'},
                                           {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA'}}]}
    history = chat(('user', TOOL + '\n\nReturn KEYA'), ('assistant', reply(first)))['messages']
    native = s.complete(None, None, None, {'messages': history + [picture]})
    assert native['orchid']['reason'] == 'content part image_url'
    later = s.complete(None, None, None, {'messages': history + [
        picture, {'role': 'assistant', 'content': 'native answer'}, {'role': 'user', 'content': 'Return KEYB'}]})
    assert reply(later) == '22222' and later['orchid']['thread'] == first['orchid']['thread']
    s.close()


def test_sampling_stop_and_long_max_tokens_reach_the_lean_target(tmp_path):
    s, r = service(tmp_path)
    result = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA and KEYB'),
                                               temperature=0.7, top_p=0.9, seed=3, max_tokens=50000, stop=['2222']))
    assert r.target.seen[-1] == {'temperature': 0.7, 'top_p': 0.9, 'seed': 3}
    assert 1 <= r.target.maxima[-1] <= 60000 - 1024  # Clamped to the target window.
    assert reply(result) == '11111,' and result['choices'][0]['finish_reason'] == 'stop'
    s.close()


def test_short_new_conversations_go_native_until_they_grow(tmp_path):
    s, r = service(tmp_path, native_below_tokens=200)
    short = s.complete(None, None, None, chat(('user', 'Return KEYA')))
    assert short['orchid'] == {'engine': 'native', 'reason': 'short conversation', 'thread': short['orchid']['thread']}
    grown = s.complete(None, None, None, chat(('user', 'Return KEYA'), ('assistant', 'native answer'),
                                              ('user', TOOL + '\n\nReturn KEYB')))
    assert reply(grown) == '22222' and grown['orchid']['engine'] == 'lean'
    s.close()


def test_lean_failure_before_output_falls_back_to_native_and_the_thread_recovers(tmp_path):
    s, r = service(tmp_path)
    r.target.fail = True
    failed = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    assert failed['orchid']['reason'] == 'lean failure' and reply(failed) == 'native answer'
    r.target.fail = False
    later = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), ('assistant', 'native answer'),
                                              ('user', 'Return KEYB')))
    assert reply(later) == '22222'
    s.close()


def test_malformed_requests_are_rejected(tmp_path):
    s, _ = service(tmp_path)
    for body in ({'messages': []}, {'messages': [{'role': 'user', 'content': 5}]}, chat(('user', 'x'), n=2),
                 chat(('user', 'x'), stop=['a'] * 5), chat(('assistant', 'last'))):
        with pytest.raises(ValueError):
            s.complete(None, None, None, body)
    s.close()


def test_http_without_headers_streams_with_usage(tmp_path):
    s, _ = service(tmp_path)
    app = create_app(config=RuntimeConfig.from_env(db_path=str(tmp_path / 'gateway.db')), specprefill_service=s)
    with TestClient(app) as client:
        plain = client.post('/v1/chat/completions', json=chat(('user', TOOL + '\n\nReturn KEYA')))
        assert plain.status_code == 200 and reply(plain.json()) == '11111'
        streamed = client.post('/v1/chat/completions', json=chat(
            ('user', TOOL + '\n\nReturn KEYA'), ('assistant', '11111'), ('user', 'Return KEYB'),
            stream=True, stream_options={'include_usage': True}))
        rows = [json.loads(line[6:]) for line in streamed.text.splitlines()
                if line.startswith('data: {')]
        text = ''.join(r['choices'][0]['delta'].get('content') or '' for r in rows if r['choices'])
        assert text == '22222'
        assert rows[-1]['choices'] == [] and rows[-1]['usage']['completion_tokens'] > 0
        native = client.post('/v1/chat/completions', json=chat(('user', 'x'), logprobs=True, stream=True))
        assert 'native answer' in native.text
        assert client.post('/v1/chat/completions', json={'messages': []}).status_code == 400
        assert client.post('/v1/chat/completions', headers={'x-memory-project': 'p'},
                           json=chat(('user', 'x'))).status_code == 400


STRATA = '/home/distilledoreo/AI/Strata'


def responses_app(tmp_path, target=None):
    import os
    if not os.path.isdir(STRATA + '/serve'):
        pytest.skip('Strata checkout not available')
    pytest.importorskip('jinja2')  # Strata's chat frontend.
    config = tmp_path / 'strata.json'
    config.write_text(json.dumps({'cwd': STRATA}))
    s, r = service(tmp_path, config_path=str(config))
    if target is not None:
        r.target = target
    return create_app(config=RuntimeConfig.from_env(db_path=str(tmp_path / 'gateway.db')), specprefill_service=s), r


class CallingTarget(RecordingTarget):
    """Asks for a file until a tool result is in the conversation, then answers from it."""

    def __call__(self, ids, maximum):
        text = Tok().decode(ids)
        if '<tool_response>' not in text:
            self.calls.append(list(ids))
            return Tok().encode('<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>')
        return super().__call__(ids, maximum)


def test_responses_api_for_codex_streams_text_and_tool_calls(tmp_path):
    app, _ = responses_app(tmp_path, CallingTarget())
    tools = [{'type': 'function', 'name': 'read_file', 'description': 'Read a file',
              'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}}}]
    first = {'model': 'orchid-flash-next-specprefill', 'instructions': 'Use excerpts.', 'stream': True,
             'store': False, 'tools': tools, 'reasoning': {'effort': 'medium'},
             'input': [{'type': 'message', 'role': 'user',
                        'content': [{'type': 'input_text', 'text': 'Return KEYA from a.py'}]}]}
    with TestClient(app) as client:
        streamed = client.post('/v1/responses', json=first)
        events = [json.loads(line[6:]) for line in streamed.text.splitlines() if line.startswith('data: ')]
        kinds = [e['type'] for e in events]
        assert kinds[:2] == ['response.created', 'response.in_progress'] and kinds[-1] == 'response.completed'
        call = next(item for item in events[-1]['response']['output'] if item['type'] == 'function_call')
        assert call['name'] == 'read_file' and json.loads(call['arguments']) == {'path': 'a.py'}
        second = {**first, 'stream': False, 'input': first['input'] + [
            {'type': 'function_call', 'call_id': call['call_id'], 'name': 'read_file', 'arguments': call['arguments']},
            {'type': 'function_call_output', 'call_id': call['call_id'], 'output': TOOL}]}
        answer = client.post('/v1/responses', json=second).json()
        assert answer['status'] == 'completed'
        text = ''.join(part['text'] for item in answer['output'] if item['type'] == 'message'
                       for part in item['content'])
        assert text == '11111' and answer['usage']['output_tokens'] > 0
        bad = client.post('/v1/responses', json={**first, 'previous_response_id': 'resp_1'})
        assert bad.status_code == 400


def test_unlimited_max_tokens_from_llama_cpp_style_clients_uses_the_default(tmp_path):
    s, r = service(tmp_path, output_tokens=777)
    s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), max_tokens=-1))
    assert r.target.maxima[-1] == 777
    s.close()


def test_any_field_that_changes_the_answer_defeats_replay(tmp_path):
    s, r = service(tmp_path)
    s.complete(None, None, None, chat(('user', 'x'), logprobs=True))
    s.complete(None, None, None, chat(('user', 'x'), logprobs=True, chat_template_kwargs={'enable_thinking': False}))
    s.complete(None, None, None, chat(('user', 'x'), logprobs=True, stream=True))  # Same answer, streamed: replay.
    assert len(r.native_calls) == 2
    s.close()


class TalkingCaller(RecordingTarget):
    def __call__(self, ids, maximum):
        output = Tok().encode('Let me read it.\n<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n'
                              '</tool_call>')
        for token in output:
            if self.on_token:
                self.on_token(token)
        return output


def test_with_tools_text_before_the_call_streams_and_markup_never_does(tmp_path):
    s, r = service(tmp_path)
    r.target = TalkingCaller()
    pieces = []
    tools = [{'type': 'function', 'function': {'name': 'read_file', 'parameters': {'type': 'object'}}}]
    result = s.complete(None, None, None, chat(('user', TOOL + '\n\nRead a.py'), tools=tools), on_text=pieces.append)
    streamed = ''.join(pieces)
    assert streamed.startswith('Let me read it.') and '<tool' not in streamed and len(pieces) > 1
    assert result['choices'][0]['finish_reason'] == 'tool_calls'
    s.close()
