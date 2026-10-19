from dataclasses import replace

from test_spec_session import Student, Target, Tok, doc, thought

from memory_gateway.specprefill.service import GatewayOptions, SpecPrefillService
from memory_gateway.specprefill.spec_session import SpecSession


class Resources:
    def __init__(self):
        self.tok, self.student, self.target = Tok(), Student(), Target()
        self.selector = self.student

    def session(self, path, system, limits):
        if path.exists() or path.with_suffix('.json').exists():
            return SpecSession.restore(path, self.tok, self.student, self.target)
        return SpecSession(self.tok, self.student, self.target, system=system, limits=limits,
                           checkpoint=path)

    def close(self):
        pass


def options(tmp_path, **kwargs):
    base = GatewayOptions(str(tmp_path / 'native'), 'unused', 'unused', engine='lean',
                          context_tokens=60000, student_context_tokens=60000, answer_tokens=32,
                          new_rate=0.05, recall_tokens=200, query_split_tokens=1024,
                          query_tail_tokens=200, native_below_tokens=0,
                          target_hot_dir=str(tmp_path / 'shm'))
    return replace(base, **kwargs)


def service(tmp_path, **kwargs):
    resources = Resources()
    return SpecPrefillService(options(tmp_path, **kwargs), resources_factory=lambda: resources), resources


def messages(*turns):
    out = [{'role': 'system', 'content': 'Use excerpts.'}]
    for role, content in turns:
        out.append({'role': role, 'content': content})
    return {'messages': out}


def test_long_final_message_is_ingested_and_only_its_tail_is_verbatim(tmp_path):
    s, r = service(tmp_path)
    tool = doc({'KEYA': '11111', 'KEYB': '22222'})
    result = s.complete('p', 't', 'r1', messages(('user', tool + '\n\nReturn KEYA')))
    assert result['choices'][0]['message']['content'] == '11111'
    assert result['orchid']['engine'] == 'lean'
    sent = Tok().decode(r.target.calls[-1])
    assert 'Return KEYA' in sent and len(r.target.calls[-1]) < 0.3 * len(tool)


def test_follow_up_recalls_omitted_evidence_and_replay_is_idempotent(tmp_path):
    s, r = service(tmp_path)
    tool = doc({'KEYA': '11111', 'KEYB': '22222'})
    first = s.complete('p', 't', 'r1', messages(('user', tool + '\n\nReturn KEYA')))
    history = messages(('user', tool + '\n\nReturn KEYA'), ('assistant', first['choices'][0]['message']['content']),
                       ('user', 'Return KEYB'))
    second = s.complete('p', 't', 'r2', history)
    assert second['choices'][0]['message']['content'] == '22222'
    calls = len(r.target.calls)
    assert s.complete('p', 't', 'r2', history) == second
    assert len(r.target.calls) == calls


def test_restart_restores_lean_session_from_checkpoint(tmp_path):
    s, _ = service(tmp_path)
    tool = doc({'KEYA': '11111', 'KEYB': '22222'})
    first = s.complete('p', 't', 'r1', messages(('user', tool + '\n\nReturn KEYA')))
    s.close()
    s2, r2 = service(tmp_path)
    history = messages(('user', tool + '\n\nReturn KEYA'), ('assistant', first['choices'][0]['message']['content']),
                       ('user', 'Return KEYB'))
    assert s2.complete('p', 't', 'r2', history)['choices'][0]['message']['content'] == '22222'
    s2.close()


class ToolTarget(Target):
    """Calls read_file until asked to return a key; then answers from excerpts."""

    def __call__(self, ids, maximum):
        self.calls.append(list(ids))
        text = Tok().decode(ids)
        if 'Return KEY' not in text[text.rfind('<|im_start|>user'):]:
            return thought(ids, Tok().encode('<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>'))
        return super().__call__(ids, maximum)


TOOLS = [{'type': 'function', 'function': {'name': 'read_file', 'parameters': {'type': 'object'}}}]


def test_tool_call_round_trip_ingests_tool_result_as_source(tmp_path):
    resources = Resources()
    resources.target = ToolTarget()
    s = SpecPrefillService(options(tmp_path, answer_tokens=96, fresh_tool_tokens=8192),
                           resources_factory=lambda: resources)
    first = s.complete('p', 't', 'r1', {**messages(('user', 'What is KEYA in a.py?')), 'tools': TOOLS})
    message = first['choices'][0]
    assert message['finish_reason'] == 'tool_calls'
    call = message['message']['tool_calls'][0]
    assert call['function']['name'] == 'read_file' and message['message']['content'] is None
    tool = doc({'KEYA': '11111', 'KEYB': '22222'})
    history = {**messages(('user', 'What is KEYA in a.py?')), 'tools': TOOLS}
    history['messages'] += [message['message'], {'role': 'tool', 'tool_call_id': call['id'], 'content': tool},
                            {'role': 'user', 'content': 'Return KEYA'}]
    second = s.complete('p', 't', 'r2', history)
    assert second['choices'][0]['message']['content'] == '11111'
    # The tool result the model just asked for is delivered in full, not trimmed.
    assert tool in Tok().decode(resources.target.calls[-1])
    # A trailing tool result is accepted too.
    history['messages'] += [second['choices'][0]['message'], {'role': 'tool', 'tool_call_id': 'x', 'content': 'KEYQ=55555'}]
    third = s.complete('p', 't', 'r3', history)
    assert third['choices'][0]['finish_reason'] in ('stop', 'length', 'tool_calls')
    session = next(iter(s.sessions.values()))
    assert any(b'KEYQ=55555' in b''.join(Tok().token_bytes(t) for t in src.ids) for src in session.sources)
    assert 'Continue the task using the tool result above.' in Tok().decode(resources.target.calls[-1])
    assert 'KEYQ=55555' in Tok().decode(resources.target.calls[-1])


def test_compacting_engine_still_rejects_tools(tmp_path):
    import pytest
    s, _ = service(tmp_path, engine='compacting')
    with pytest.raises(ValueError):
        s.validate({**messages(('user', 'hi')), 'tools': TOOLS})


def test_streaming_with_tools_emits_tool_calls_without_raw_markup(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from memory_gateway.config import RuntimeConfig
    from memory_gateway.gateway import create_app
    resources = Resources()
    resources.target = ToolTarget()
    native = SpecPrefillService(options(tmp_path, answer_tokens=96), resources_factory=lambda: resources)
    app = create_app(config=RuntimeConfig.from_env(db_path=str(tmp_path / 'gateway.db')),
                     specprefill_service=native)
    body = {**messages(('user', 'What is KEYA in a.py?')), 'tools': TOOLS, 'stream': True}
    headers = {'x-memory-project': 'p', 'x-memory-thread': 't', 'x-request-id': 'r1'}
    with TestClient(app) as client:
        response = client.post('/v1/chat/completions', json=body, headers=headers)
    chunks = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith('data: {')]
    deltas = [c['choices'][0]['delta'] for c in chunks]
    assert not any('<tool_call>' in (d.get('content') or '') for d in deltas)
    assert any(d.get('tool_calls') for d in deltas)
    assert chunks[-1]['choices'][0]['finish_reason'] == 'tool_calls'


def test_tool_results_are_labelled_by_call_and_rereads_become_new_versions(tmp_path):
    resources = Resources()
    resources.target = ToolTarget()
    s = SpecPrefillService(options(tmp_path, answer_tokens=96), resources_factory=lambda: resources)
    base = {**messages(('user', 'What is KEYA in a.py?')), 'tools': TOOLS}
    first = s.complete('p', 't', 'r1', base)
    call = first['choices'][0]['message']['tool_calls'][0]
    history = dict(base)
    history['messages'] = base['messages'] + [first['choices'][0]['message'],
                                              {'role': 'tool', 'tool_call_id': call['id'], 'content': 'KEYA=11111\n'}]
    second = s.complete('p', 't', 'r2', history)
    session = next(iter(s.sessions.values()))
    assert [(x.path, x.version) for x in session.sources] == [('tool read_file {"path": "a.py"}', 1)]
    assert 'Task: What is KEYA in a.py?' in Tok().decode(resources.target.calls[-1])
    # The file changed on disk: a second read of the same call becomes v2.
    again = second['choices'][0]['message']
    call2 = again['tool_calls'][0]
    history['messages'] = history['messages'] + [again, {'role': 'tool', 'tool_call_id': call2['id'],
                                                         'content': 'KEYA=33333\n'}]
    s.complete('p', 't', 'r3', history)
    assert [(x.path, x.version) for x in session.sources][-1] == ('tool read_file {"path": "a.py"}', 2)


def test_256k_settings_come_from_environment_and_validate(monkeypatch, tmp_path):
    import pytest
    for name, value in (('ENABLED', 'true'), ('STATE_DIR', str(tmp_path)), ('CONFIG', str(tmp_path)), ('STUDENT', str(tmp_path)),
                        ('ENGINE', 'lean'), ('STUDENT_SPARSE_KEYS', '16384'), ('TARGET_KV', 'fp16'),
                        ('VRAM_RESERVE_MIB', '8192'), ('STUDENT_SPARSE_ROWS', '8192')):
        monkeypatch.setenv('ORCHID_SPECPREFILL_' + name, value)
    loaded = GatewayOptions.from_env()
    assert (loaded.student_sparse_keys, loaded.student_sparse_rows, loaded.target_kv,
            loaded.vram_reserve_mib) == (16384, 8192, 'fp16', 8192)
    with pytest.raises(ValueError, match='K/V'):
        options(tmp_path, target_kv='q4').validate()


def test_an_empty_reply_can_be_sent_back_as_history(tmp_path):
    class Blank(Target):  # a reply with no answer text, like Flash-Next's lone end-of-turn
        def __call__(self, ids, maximum):
            return super().__call__(ids, maximum) or Tok().encode(' ')
    s, r = service(tmp_path)
    r.target = Blank()
    tool = doc({'KEYA': '11111', 'KEYB': '22222'})
    first = s.complete('p', 't', 'r1', messages(('user', tool + '\n\nSay nothing')))
    said = first['choices'][0]['message']['content']
    assert not said.strip()
    history = messages(('user', tool + '\n\nSay nothing'), ('assistant', said), ('user', 'Return KEYB'))
    assert s.complete('p', 't', 'r2', history)['choices'][0]['message']['content'] == '22222'
    with __import__('pytest').raises(ValueError):
        s.complete('p', 't2', 'r3', messages(('user', '   ')))


def test_fresh_tool_results_can_be_trimmed_like_any_input(tmp_path):
    resources = Resources()
    resources.target = ToolTarget()
    s = SpecPrefillService(options(tmp_path, answer_tokens=96, fresh_tool_tokens=0),
                           resources_factory=lambda: resources)
    first = s.complete('p', 't', 'r1', {**messages(('user', 'What is KEYA in a.py?')), 'tools': TOOLS})
    call = first['choices'][0]['message']['tool_calls'][0]
    tool = doc({'KEYA': '11111', 'KEYB': '22222'})
    history = {**messages(('user', 'What is KEYA in a.py?')), 'tools': TOOLS}
    history['messages'] += [first['choices'][0]['message'], {'role': 'tool', 'tool_call_id': call['id'], 'content': tool},
                            {'role': 'user', 'content': 'Return KEYA'}]
    assert s.complete('p', 't', 'r2', history)['choices'][0]['message']['content'] == '11111'
    assert tool not in Tok().decode(resources.target.calls[-1])  # Trimmed: excerpts only.


def test_tools_use_flash_next_native_call_format():
    import json

    from memory_gateway.specprefill.tools import message_text, parse, system_text
    tools = [{'type': 'function', 'function': {'name': 'exec_command', 'parameters': {
        'type': 'object', 'properties': {'cmd': {'type': 'string'}, 'timeout': {'type': 'number'}}}}}]
    system = system_text('Be brief.', tools)
    assert system.startswith('# Tools\n\nYou have access to the following functions:\n\n<tools>\n{"name": "exec_command"')
    assert '<function=example_function_name>' in system and system.endswith('</IMPORTANT>\n\nBe brief.')
    text = ('Checking.\n\n<tool_call>\n<function=exec_command>\n<parameter=cmd>\ngrep -rn "class X" .\n'
            '</parameter>\n<parameter=timeout>\n30\n</parameter>\n</function>\n</tool_call>')
    content, [call] = parse(text, 'seed', tools)
    assert content == 'Checking.' and call['function']['name'] == 'exec_command'
    assert json.loads(call['function']['arguments']) == {'cmd': 'grep -rn "class X" .', 'timeout': 30}
    # The history renders it back the same way, and JSON bodies are still read.
    assert message_text({'role': 'assistant', 'content': 'Checking.', 'tool_calls': [call]}) == text
    assert parse('<tool_call>\n{"name": "exec_command", "arguments": {"cmd": "ls"}}\n</tool_call>', 's', tools)[1]
    # A call cut off before its closing tags still counts; an undeclared tool doesn't.
    assert parse('<tool_call>\n<function=exec_command>\n<parameter=cmd>\nls\n', 's', tools)[1]
    assert parse('<tool_call>\n<function=rm_all>\n</function>\n</tool_call>', 's', tools)[1] == []


def test_lean_prompt_frame_matches_native_flash_next_template(tmp_path):
    import json

    import pytest
    pytest.importorskip('jinja2')
    from dataclasses import replace
    from pathlib import Path
    config = Path(__file__).parent.parent / 'data/strata_specprefill/longctx-32kctx-alone.json'
    cwd = Path(json.loads(config.read_text())['cwd']) if config.exists() else None
    if not cwd or not (cwd / 'serve' / 'frontend.py').exists():
        pytest.skip('the Strata checkout is not on this machine')
    s = SpecPrefillService(replace(options(tmp_path), config_path=str(config)), resources_factory=Resources)
    template = s._chat_template()
    for settings in ({}, {'reasoning_effort': 'medium'}, {'enable_thinking': False}):
        frame = s._system_frame('Be brief.', TOOLS, settings)
        native = template.render([{'role': 'system', 'content': 'Be brief.'}, {'role': 'user', 'content': 'Hi'}],
                                 tools=[t['function'] for t in TOOLS], **settings)
        session = SpecSession(Tok(), Student(), Target(), system=frame)
        lean = Tok().decode(session._frame('system', frame) + session._query(
            'Hi', settings.get('enable_thinking', True) is not False))
        assert lean == native, settings


def test_a_tool_step_is_never_a_whole_history_request(tmp_path):
    resources = Resources()
    resources.target = ToolTarget()
    resources.native = lambda payload, on_text=None: {
        'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {
                'name': 'read_file', 'arguments': '{"path": "a.py"}'}}]}}], 'usage': {}}
    s = SpecPrefillService(options(tmp_path, answer_tokens=96), resources_factory=lambda: resources)
    ask = 'Summarize every file you read. What is KEYA in a.py?'
    first = s.complete('p', 't', 'r1', {**messages(('user', ask)), 'tools': TOOLS})
    assert first['orchid']['reason'] == 'whole history'  # The user's own question: native reads it all.
    call = first['choices'][0]['message']['tool_calls'][0]
    history = {**messages(('user', ask)), 'tools': TOOLS}
    history['messages'] += [first['choices'][0]['message'],
                            {'role': 'tool', 'tool_call_id': call['id'], 'content': 'KEYQ=55555'}]
    step = s.complete('p', 't', 'r2', history)
    assert step['orchid']['engine'] == 'lean'  # A step of the same task: not a new whole-history question,
    assert not next(iter(s.sessions.values())).events[-1]['broad']  # and no broad evidence either.
