"""Phase 6 load limits: a queue limit with a clear busy error, a per-request timeout, and
cancellation when the client goes away."""
import threading
import time

import pytest
from fastapi.testclient import TestClient
from test_spec_session import Target, Tok, thought
from test_specprefill_gateway_auto import TOOL, chat, service

from memory_gateway.config import RuntimeConfig
from memory_gateway.gateway import create_app
from memory_gateway.specprefill.service import Busy


class SlowTarget(Target):
    """Writes one token at a time until stopped (the request's abort, or its own cancel)."""

    def __init__(self):
        super().__init__()
        self.on_token, self.cancel, self.abort = None, threading.Event(), None
        self.started, self.slow = threading.Event(), True

    def __call__(self, ids, maximum):
        if not self.slow:
            return super().__call__(ids, maximum)
        self.calls.append(list(ids))
        out = thought(ids, [])
        self.started.set()
        for _ in range(maximum):
            out += Tok().encode('a')
            if self.on_token:
                self.on_token(out[-1])
            time.sleep(0.01)
            if self.cancel.is_set() or (self.abort is not None and self.abort.is_set()):
                break
        return out


def test_requests_beyond_the_queue_limit_are_told_the_gateway_is_busy(tmp_path):
    s, r = service(tmp_path, queue_limit=1, answer_tokens=4000, output_tokens=4000)
    r.target = SlowTarget()
    cancel = threading.Event()
    first = threading.Thread(target=s.complete, args=(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'))),
                             kwargs={'cancel': cancel})
    first.start()
    assert r.target.started.wait(10)
    with pytest.raises(Busy):
        s.complete(None, None, None, chat(('user', 'Other. ' + TOOL + '\n\nReturn KEYB')))
    cancel.set()
    first.join(10)
    assert not first.is_alive()
    s.close()


def test_a_cancelled_request_stops_generating_and_the_thread_stays_usable(tmp_path):
    s, r = service(tmp_path, answer_tokens=4000, output_tokens=4000)
    r.target = SlowTarget()
    cancel, result = threading.Event(), {}
    worker = threading.Thread(target=lambda: result.update(s.complete(
        None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')), cancel=cancel)))
    worker.start()
    assert r.target.started.wait(10)
    time.sleep(0.2)
    cancel.set()
    worker.join(10)
    assert not worker.is_alive() and 0 < result['usage']['completion_tokens'] < 4000  # Stopped part way.
    r.target.slow = False  # The thread's next turn answers normally.
    again = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), ('user', 'Return KEYB')))
    assert again['choices'][0]['message']['content'] == '22222'
    s.close()


def test_a_request_that_runs_too_long_is_stopped(tmp_path):
    s, r = service(tmp_path, request_timeout_s=0.5, answer_tokens=4000, output_tokens=4000)
    r.target = SlowTarget()
    started = time.perf_counter()
    result = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    assert time.perf_counter() - started < 5 and result['usage']['completion_tokens'] < 4000
    s.close()


def test_http_busy_is_a_503_with_retry_after(tmp_path):
    s, r = service(tmp_path, queue_limit=1, answer_tokens=300, output_tokens=300)
    r.target = SlowTarget()
    app = create_app(config=RuntimeConfig.from_env(db_path=str(tmp_path / 'g.db')), specprefill_service=s)
    with TestClient(app) as client:
        first = threading.Thread(target=client.post, args=('/v1/chat/completions',),
                                 kwargs={'json': chat(('user', TOOL + '\n\nReturn KEYA'))})
        first.start()
        assert r.target.started.wait(10)
        busy = client.post('/v1/chat/completions', json=chat(('user', 'Other. ' + TOOL + '\n\nReturn KEYB')))
        assert busy.status_code == 503 and busy.json()['error']['type'] == 'busy'
        assert busy.headers['retry-after'] == '5'
        first.join(30)
    s.close()


def test_metrics_count_turns_and_times_with_no_conversation_text(tmp_path):
    import json
    secret = 'QUOKKA-SECRET-5150'
    s, r = service(tmp_path)
    app = create_app(config=RuntimeConfig.from_env(db_path=str(tmp_path / 'g.db')), specprefill_service=s)
    with TestClient(app) as client:
        client.post('/v1/chat/completions', json=chat(('user', f'{TOOL}\nThe code is {secret}.\n\nReturn KEYA')))
        client.post('/v1/chat/completions', json=chat(('user', f'{TOOL}\nThe code is {secret}.\n\nReturn KEYA'),
                                                      ('assistant', '11111'), ('user', 'Return KEYB')))
        metrics = client.get('/v1/orchid/metrics').json()
    assert metrics['turns'] == {'lean': 2} and metrics['waiting'] == 0
    assert metrics['seconds']['wall'] > 0 and 'student' in metrics['seconds'] and metrics['tokens']['evidence'] > 0
    assert metrics['storage']['archive_bytes'] > 0
    assert secret not in json.dumps(metrics) and 'KEYA' not in json.dumps(metrics)
    s.close()


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def test_idle_caches_expire_and_idle_archives_are_compacted_on_a_fake_clock(tmp_path):
    s, r = service(tmp_path)
    s.clock = clock = Clock()
    a = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    key = a['orchid']['thread']
    for path in (s.directory / 'student-cache' / f'{key}.pt', s._target_file(key), s._target_hot(key)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * 1000)
    clock.now += 86400 * 10
    b = s.complete(None, None, None, chat(('user', 'Other. ' + TOOL + '\n\nReturn KEYB')))  # Thread B, now.
    s.sessions.pop(key).close()  # A is no longer resident (as after a restart or eviction).
    s.active_key = s.target_owner = b['orchid']['thread']
    assert s.maintain()['caches_removed'] == 0  # A has been idle 10 days: under the 14-day TTL.
    clock.now += 86400 * 5
    done = s.maintain()
    assert done['caches_removed'] == 3 and done['cache_bytes'] == 3000 and done['archives_compacted'] == 0
    assert s._session_path(key).exists()  # The archive stays ...
    again = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), ('assistant', '11111'),
                                              ('user', 'Return KEYC')))
    assert again['choices'][0]['message']['content'] == '33333' and again['orchid']['thread'] == key  # ... and resumes.
    s.sessions.pop(key).close()
    s.active_key = s.target_owner = b['orchid']['thread']
    clock.now += 86400 * 31
    assert s.maintain()['archives_compacted'] == 1  # A: idle past 30 days (B is in use).
    assert s.maintain()['archives_compacted'] == 0  # Once, until it is used again.
    later = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), ('assistant', '11111'),
                                              ('user', 'Return KEYC'), ('assistant', '33333'),
                                              ('user', 'Return KEYB')))
    assert later['choices'][0]['message']['content'] == '22222'  # The compacted archive reads as before.
    s.close()


def test_another_state_directorys_idle_hot_files_are_removed_at_maintenance(tmp_path):
    import os
    shm = tmp_path / 'shm'
    s, r = service(tmp_path, target_hot_dir=str(shm))
    s.clock = clock = Clock()
    own = s._target_hot('mine')
    own.parent.mkdir(parents=True)
    own.write_bytes(b'x' * 10)
    stale, recent = shm / f'orchid-{os.getuid()}-aaaaaaaaaaaa', shm / f'orchid-{os.getuid()}-bbbbbbbbbbbb'
    other_user = shm / f'orchid-{os.getuid() + 1}-cccccccccccc'
    for directory in (stale, recent, other_user):
        directory.mkdir(parents=True)
        (directory / 'k.bin').write_bytes(b'x' * 100)
    old = clock.now - 25 * 3600
    for path in (own, stale / 'k.bin', other_user / 'k.bin'):
        os.utime(path, (old, old))
    os.utime(recent / 'k.bin', (clock.now - 3600, clock.now - 3600))
    done = s.maintain()
    assert done['stale_hot_dirs'] == 1 and done['stale_hot_bytes'] == 100
    assert not stale.exists() and recent.exists() and other_user.exists() and own.exists()
    s.close()
