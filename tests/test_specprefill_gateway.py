import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_strata_compacting_session import Selector, Target, Tok, preserve

from memory_gateway.config import RuntimeConfig
from memory_gateway.gateway import create_app
from memory_gateway.specprefill.compacting_session import CompactingSession
from memory_gateway.specprefill.scheduling import CapacityError, CompactionPolicy
from memory_gateway.specprefill.service import (
    GatewayOptions,
    RequestConflict,
    SpecPrefillService,
    digest,
)


class StreamingTarget(Target):
    def __init__(self):
        super().__init__()
        self.on_token = None
        self.entered = threading.Event()
        self.release = None

    def __call__(self, ids, maximum):
        self.entered.set()
        if self.release:
            assert self.release.wait(5)
        output = super().__call__(ids, maximum)
        for token in output:
            if self.on_token:
                self.on_token(token)
        return output


class Resources:
    def __init__(self):
        self.tok, self.selector, self.target = Tok(), Selector(), StreamingTarget()
        self.closed = False

    def session(self, path, system, limits):
        assert not self.closed
        if path.exists():
            return CompactingSession.restore(path, self.tok, self.selector, self.target, preserve)
        return CompactingSession(self.tok, self.selector, self.target, preserve,
                                 system=system, limits=limits, checkpoint=path)

    def close(self):
        self.closed = True
        self.selector.reset()


def options(tmp_path, **kwargs):
    base = GatewayOptions(str(tmp_path / 'native'), 'unused', 'unused', context_tokens=12000,
                          compact_tokens=2200, expansion_tokens=1400, summary_tokens=1200,
                          high_water=.01)
    return replace(base, **kwargs)


def service(tmp_path, **kwargs):
    resources = Resources()
    return SpecPrefillService(options(tmp_path, **kwargs), resources_factory=lambda: resources), resources


def payload(question='SECRET=84317. What is the secret?', messages=None, **kwargs):
    return {'messages': messages or [{'role': 'system', 'content': 'Use exact evidence.'},
                                     {'role': 'user', 'content': question}], **kwargs}


HEADERS = {'x-memory-project': 'p', 'x-memory-thread': 't', 'x-request-id': 'r1'}


def app_for(tmp_path, native):
    runtime = RuntimeConfig.from_env(db_path=str(tmp_path / 'gateway.db'))
    return create_app(config=runtime, specprefill_service=native)


def test_automatic_compaction_preserves_history_and_request_replay(tmp_path):
    s, r = service(tmp_path)
    first = s.complete('p', 't', 'r1', payload())
    history = payload()['messages'] + [first['choices'][0]['message'], {'role': 'user', 'content': 'Repeat the secret'}]
    second = s.complete('p', 't', 'r2', payload(messages=history))
    assert second['choices'][0]['message']['content'] == '84317'
    assert second['orchid']['generation'] >= 1
    count = len(r.target.calls)
    assert s.complete('p', 't', 'r2', payload(messages=history)) == second
    assert len(r.target.calls) == count
    with pytest.raises(RequestConflict):
        s.complete('p', 't', 'r2', payload('different'))
    assert len(s.sessions[digest(['p', 't'])].archive.sources) >= 4
    s.close()


def test_history_branch_is_rejected_before_another_model_call(tmp_path):
    s, r = service(tmp_path)
    s.complete('p', 't', 'r1', payload())
    count = len(r.target.calls)
    changed = payload(messages=[{'role': 'system', 'content': 'Changed!'}, {'role': 'user', 'content': 'Repeat'}])
    with pytest.raises(RequestConflict):
        s.complete('p', 't', 'r2', changed)
    assert len(r.target.calls) == count
    s.close()


def test_failed_answer_can_be_retried_and_other_requests_cannot_overtake_it(tmp_path):
    s, r = service(tmp_path)
    r.target.fail = True
    with pytest.raises(RuntimeError):
        s.complete('p', 't', 'r1', payload())
    with pytest.raises(RequestConflict, match='prior request'):
        s.complete('p', 't', 'r2', payload())
    r.target.fail = False
    answer = s.complete('p', 't', 'r1', payload())
    assert answer['choices'][0]['message']['content'] == '84317'
    assert s.sessions[digest(['p', 't'])].turn == 1
    s.close()


def test_restart_recovers_checkpointed_answer_without_model_replay(tmp_path):
    s, r = service(tmp_path)
    request = payload()
    first = s.complete('p', 't', 'r1', request)
    key = digest(['p', 't'])
    pending = {'rid': 'r1', 'hash': digest(request), 'turn': 0, 'base': 0,
               'appended': [digest(m) for m in request['messages']]}
    # Crash window: checkpoint committed but completion-ledger transaction absent.
    with s.db:
        s.db.execute('DELETE FROM completions')
        s.db.execute('DELETE FROM history_entries WHERE key=? AND seq>=?', (key, 0))
        s.db.execute('UPDATE sessions SET pending=? WHERE key=?', (json.dumps(pending), key))
    s.close()
    other, new_resource = service(tmp_path)
    recovered = other.complete('p', 't', 'r1', request)
    assert recovered['choices'] == first['choices']
    assert not new_resource.target.calls
    assert other.sessions[key].turn == 1
    other.close()


def test_history_ledger_appends_incrementally_without_snapshot_rewrites(tmp_path):
    s, _ = service(tmp_path)
    first = s.complete('p', 't', 'r1', payload())
    history = payload()['messages'] + [first['choices'][0]['message'], {'role': 'user', 'content': 'Repeat the secret'}]
    second = s.complete('p', 't', 'r2', payload(messages=history))
    assert second['choices'][0]['message']['content'] == '84317'
    key = digest(['p', 't'])
    entries = s.db.execute('SELECT seq,hash FROM history_entries WHERE key=? ORDER BY seq', (key,)).fetchall()
    assert [seq for seq, _ in entries] == list(range(len(entries)))
    assert [h for _, h in entries] == [digest(m) for m in history] + [digest(second['choices'][0]['message'])]
    columns = [row[1] for row in s.db.execute('PRAGMA table_info(sessions)')]
    assert 'history' not in columns
    pending = s.db.execute('SELECT pending FROM sessions WHERE key=?', (key,)).fetchone()[0]
    assert pending is None
    s.close()
    # Ledger persistence, not in-memory state, carries the conversation across restarts.
    other, _ = service(tmp_path)
    continued = other.complete('p', 't', 'r3', payload(messages=[{'role': 'user', 'content': 'Repeat the secret'}]))
    assert continued['choices'][0]['message']['content'] == '84317'
    count = other.db.execute('SELECT COUNT(*) FROM history_entries WHERE key=?', (key,)).fetchone()[0]
    assert count == len(entries) + 2
    other.close()


def test_legacy_snapshot_database_migrates_to_incremental_ledger(tmp_path):
    import sqlite3
    key = digest(['p', 't'])
    request = payload()
    snapshot = [digest(request['messages'][0])]
    directory = tmp_path / 'native'
    directory.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(directory / 'gateway.sqlite3')
    legacy.executescript('''
        CREATE TABLE sessions(key TEXT PRIMARY KEY, history TEXT NOT NULL,
            system TEXT NOT NULL, pending TEXT, growth INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE completions(key TEXT NOT NULL, rid TEXT NOT NULL,
            hash TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(key,rid));
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    legacy.execute('INSERT INTO sessions VALUES (?,?,?,NULL,?)',
                   (key, json.dumps(snapshot), 'Use exact evidence.', 0))
    legacy.commit()
    legacy.close()
    s, _ = service(tmp_path)
    assert s.db.execute('SELECT COUNT(*) FROM history_entries WHERE key=?', (key,)).fetchone()[0] == len(snapshot)
    columns = [row[1] for row in s.db.execute('PRAGMA table_info(sessions)')]
    assert 'history' not in columns
    history = request['messages'] + [{'role': 'assistant', 'content': 'Acknowledged.'},
                                     {'role': 'user', 'content': 'Repeat the secret'}]
    answer = s.complete('p', 't', 'r2', payload(messages=history))
    assert answer['choices'][0]['message']['content'] == '84317'
    s.close()


def test_owner_lock_and_resident_session_eviction(tmp_path):
    s, r = service(tmp_path, resident_sessions=1)
    with pytest.raises(RuntimeError, match='owner'):
        service(tmp_path)
    s.complete('p', 't1', 'r1', payload())
    s.complete('p', 't2', 'r1', payload())
    assert len(s.sessions) == 1
    s.complete('p', 't1', 'r2', payload(messages=[{'role': 'user', 'content': 'Repeat the secret'}]))
    assert s.sessions[digest(['p', 't1'])].turn == 2
    assert r.selector.resets >= 3
    s.close()


def test_native_requests_serialize_across_threads(tmp_path):
    s, _ = service(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(s.complete, 'p', 't', 'r1', payload()) for _ in range(2)]
        result = [f.result() for f in futures]
    assert result[0] == result[1]
    assert s.sessions[digest(['p', 't'])].turn == 1
    s.close()


def test_http_json_and_sse_use_native_route_and_do_not_stream_compaction_probes(tmp_path):
    s, _ = service(tmp_path)
    app = app_for(tmp_path, s)
    with TestClient(app) as client:
        assert client.get('/healthz').json()['compaction'] == 'specprefill'
        assert client.get('/v1/models').json()['data'][0]['id'] == s.options.model
        first = client.post('/v1/chat/completions', headers=HEADERS, json=payload())
        assert first.status_code == 200
        streamed = client.post('/v1/chat/completions', headers={**HEADERS, 'x-request-id': 'r2'},
            json=payload(messages=[{'role': 'user', 'content': 'Repeat the secret'}], stream=True))
        assert streamed.status_code == 200
        rows = [json.loads(line[6:]) for line in streamed.text.splitlines()
                if line.startswith('data: ') and line != 'data: [DONE]']
        text = ''.join(row['choices'][0]['delta'].get('content', '') for row in rows)
        assert text == '84317'  # No one-token target rebuild probe in the stream.
        assert streamed.text.endswith('data: [DONE]\n\n')
        assert app.state.compaction_worker is None
        events = app.state.store.list_events('specprefill-' + digest(['p', 't']))
        assert any(e['role'] == 'assistant' for e in events)
    assert s.closed


@pytest.mark.parametrize('request_body', [payload(tools=[]), payload(temperature=1), payload(n=2),
    payload(max_tokens=99999), {'messages': [42]}, payload(messages=[{'role':'user','content':[] }])])
def test_unsupported_requests_fail_before_loading_models(tmp_path, request_body):
    calls = []
    s = SpecPrefillService(options(tmp_path), resources_factory=lambda: calls.append(True))
    with TestClient(app_for(tmp_path, s)) as client:
        response = client.post('/v1/chat/completions', headers=HEADERS, json=request_body)
        assert response.status_code == 400
    assert not calls


def test_http_requires_scope_and_keeps_projects_with_same_thread_isolated(tmp_path):
    s, _ = service(tmp_path)
    with TestClient(app_for(tmp_path, s)) as client:
        assert client.post('/v1/chat/completions', json=payload()).status_code == 400
        for project in ('p1', 'p2'):
            reply = client.post('/v1/chat/completions', headers={**HEADERS,'x-memory-project':project}, json=payload())
            assert reply.status_code == 200
        assert len(s.sessions) == 2
        for project in ('p1', 'p2'):
            assert client.app.state.store.list_events('specprefill-' + digest([project, 't']))


def test_health_remains_responsive_during_native_inference(tmp_path):
    s, r = service(tmp_path)
    r.target.release = threading.Event()
    app = app_for(tmp_path, s)
    async def scenario():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                pending = asyncio.create_task(client.post('/v1/chat/completions', headers=HEADERS, json=payload()))
                assert await asyncio.to_thread(r.target.entered.wait, 2)
                health = await asyncio.wait_for(client.get('/healthz'), timeout=1)
                assert health.status_code == 200
                r.target.release.set()
                assert (await pending).status_code == 200
    asyncio.run(scenario())


def test_scheduler_rejects_unrecoverable_capacity_and_never_loops_compaction(tmp_path):
    s, _ = service(tmp_path)
    with pytest.raises(CapacityError):
        s.complete('p', 't', 'too-large', payload('x' * 15000))
    assert s.db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
    s.complete('p', 't', 'good', payload())
    session = s.sessions[digest(['p','t'])]
    before = session.generation
    with pytest.raises(CapacityError):
        CompactionPolicy(.01).prepare(session, 'Continue', incoming_tokens=15000)
    assert session.generation == before + 1
    s.close()


def test_environment_switch_is_opt_in_and_validates_paths(monkeypatch):
    monkeypatch.delenv('ORCHID_SPECPREFILL_ENABLED', raising=False)
    assert GatewayOptions.from_env() is None
    monkeypatch.setenv('ORCHID_SPECPREFILL_ENABLED', 'true')
    for name in ('STATE_DIR', 'CONFIG', 'STUDENT'):
        monkeypatch.delenv('ORCHID_SPECPREFILL_' + name, raising=False)
    with pytest.raises(ValueError, match='paths'):
        GatewayOptions.from_env()


def test_large_imported_history_is_archived_in_sections_and_scheduled_before_overflow(tmp_path):
    s, _ = service(tmp_path)
    source = 'SECRET=84317\n' + 'routine filler\n' * 1100
    request = payload(messages=[{'role':'system','content':'Use exact evidence.'},
        {'role':'user','content':source}, {'role':'assistant','content':'Acknowledged.'},
        {'role':'user','content':'What is SECRET?'}])
    answer = s.complete('p','t','r1',request)
    session = s.sessions[digest(['p','t'])]
    sections = [item['text'] for item in session.archive.sources.values()
                if item['path'].startswith('conversation/import/0/user/')]
    assert ''.join(sections) == source
    assert len(sections) > 1
    assert session.generation > 0
    assert answer['choices'][0]['message']['content'] == '84317'
    s.close()


def test_scheduler_detects_exhausted_summarizer_headroom_before_calling_models(tmp_path):
    s, r = service(tmp_path)
    s.complete('p','t','r1',payload())
    session = s.sessions[digest(['p','t'])]
    class NoRoom:
        output_tokens = 768
        def input_ids(self, request):
            return [0] * session.limits.target_context
        def __call__(self, request):
            pytest.fail('Summarizer must not be invoked without headroom')
    session.summarize = NoRoom()
    count = len(r.target.calls)
    with pytest.raises(CapacityError, match='headroom'):
        s.policy.prepare(session, 'Continue')
    assert len(r.target.calls) == count
    s.close()


def test_disconnect_detaches_stream_but_finishes_and_checkpoints_one_turn(tmp_path):
    s, r = service(tmp_path)
    r.target.release = threading.Event()
    app = app_for(tmp_path, s)
    async def scenario():
        async with app.router.lifespan_context(app):
            disconnected = asyncio.Event()
            initial = True
            async def receive():
                nonlocal initial
                if initial:
                    initial = False
                    return {'type':'http.request','body':json.dumps(payload(stream=True)).encode(),'more_body':False}
                await disconnected.wait()
                return {'type':'http.disconnect'}
            async def send(message):
                if message['type'] == 'http.response.body' and b'assistant' in message.get('body', b''):
                    disconnected.set()
            scope = {'type':'http','asgi':{'version':'3.0','spec_version':'2.3'}, 'http_version':'1.1',
                'method':'POST','scheme':'http','path':'/v1/chat/completions', 'raw_path':b'/v1/chat/completions',
                'query_string':b'', 'root_path':'', 'server':('test',80),'client':('test',123),
                'headers':[(k.encode(),v.encode()) for k,v in {**HEADERS,'content-type':'application/json'}.items()]}
            await asyncio.wait_for(app(scope, receive, send), timeout=2)
            assert disconnected.is_set()
            assert await asyncio.to_thread(r.target.entered.wait, 2)
            r.target.release.set()
            if app.state.specprefill_tasks:
                await asyncio.wait_for(asyncio.gather(*app.state.specprefill_tasks), timeout=2)
            assert s.sessions[digest(['p','t'])].turn == 1
            assert s.db.execute('SELECT COUNT(*) FROM completions').fetchone()[0] == 1
    asyncio.run(scenario())


def test_native_state_identity_rejects_model_changes_but_allows_scheduling_changes(tmp_path):
    s, _ = service(tmp_path)
    s.complete('p','t','r1',payload())
    s.close()
    with pytest.raises(RequestConflict, match='configuration/model/budgets'):
        SpecPrefillService(options(tmp_path, model='different-model'), resources_factory=Resources)
    # Ownership was released even when identity validation failed.
    changed = SpecPrefillService(options(tmp_path, high_water=.60, resident_sessions=2), resources_factory=Resources)
    assert changed.complete('p','t','r1',payload())['choices'][0]['message']['content'] == '84317'
    changed.close()
