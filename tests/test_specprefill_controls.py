"""Phase 3 controls: list, export, delete (nothing recoverable), purge, quota, disk full, kill mid-write."""
import errno
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from test_specprefill_gateway_auto import TOOL, chat, reply, service

from memory_gateway.config import RuntimeConfig
from memory_gateway.gateway import create_app

SECRET = 'ZEBRA-QUOKKA-7731'


def app_for(tmp_path, s):
    return create_app(config=RuntimeConfig.from_env(db_path=str(tmp_path / 'gateway.db')), specprefill_service=s)


def files_containing(root, needle):
    hits = []
    for path in Path(root).rglob('*'):
        if path.is_file() and needle.encode() in path.read_bytes():
            hits.append(str(path))
    return hits


def test_list_export_and_delete_leave_nothing_recoverable(tmp_path):
    s, _ = service(tmp_path)
    with TestClient(app_for(tmp_path, s)) as client:
        secret = chat(('user', f'{TOOL}\nThe vault code is {SECRET}.\n\nReturn KEYA'))
        a = client.post('/v1/chat/completions', json=secret).json()
        client.post('/v1/chat/completions', json=chat(('user', TOOL + '\n\nReturn KEYB')))
        rows = client.get('/v1/orchid/threads').json()['data']
        assert len(rows) == 2 and all(r['turns'] == 1 and r['archive_bytes'] > 0 for r in rows)
        thread = a['orchid']['thread']
        exported = client.get(f'/v1/orchid/threads/{thread}/export').text.splitlines()
        assert json.loads(exported[0])['thread'] == thread
        assert any(SECRET in json.loads(line).get('content') or '' for line in exported[1:])
        assert files_containing(tmp_path, SECRET)  # Stored somewhere before the delete ...
        removed = client.delete(f'/v1/orchid/threads/{thread}').json()
        assert removed['thread'] == thread and removed['events'] >= 2
        assert [r['thread'] for r in client.get('/v1/orchid/threads').json()['data']] != [thread]
        assert len(client.get('/v1/orchid/threads').json()['data']) == 1
        assert client.get(f'/v1/orchid/threads/{thread}/export').status_code == 404
        assert client.delete(f'/v1/orchid/threads/{thread}').status_code == 404
        assert files_containing(tmp_path, SECRET) == []  # ... and nowhere after it.
        again = client.post('/v1/chat/completions', json=chat(('user', TOOL + '\n\nReturn KEYB'),
                                                                ('assistant', '22222'), ('user', 'Return KEYC')))
        assert reply(again.json()) == '33333'  # The other thread is untouched.


def test_purge_caches_removes_only_caches(tmp_path):
    s, _ = service(tmp_path)
    s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    for directory in (s.directory / 'student-cache', s.directory / 'target-cache', s._target_hot('x').parent):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'k.bin').write_bytes(b'x' * 100)
    with TestClient(app_for(tmp_path, s)) as client:
        assert client.post('/v1/orchid/caches/purge').json()['bytes'] == 300
        assert client.get('/v1/orchid/storage').json()['cache_bytes'] == 0
        assert len(client.get('/v1/orchid/threads').json()['data']) == 1  # The archive stays.


def test_quota_warns_at_80_percent_and_goes_native_when_full(tmp_path):
    s, r = service(tmp_path)
    s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    from memory_gateway.specprefill.controls import storage
    used = storage(s)['archive_bytes']
    from dataclasses import replace
    s.options = replace(s.options, state_quota_mib=used / 0.9 / 2**20)  # 90% used.
    warned = s.complete(None, None, None, chat(('user', 'Other. ' + TOOL + '\n\nReturn KEYB')))
    assert warned['orchid']['engine'] == 'lean' and warned['orchid'].get('storage_warning')
    s.options = replace(s.options, state_quota_mib=used / 2 / 2**20)  # Over the quota.
    full = s.complete(None, None, None, chat(('user', 'Third. ' + TOOL + '\n\nReturn KEYC')))
    assert full['orchid']['engine'] == 'native' and full['orchid']['reason'] == 'storage quota'


def test_disk_full_goes_native_and_a_failed_write_keeps_the_thread_consistent(tmp_path, monkeypatch):
    import shutil
    s, r = service(tmp_path)
    first = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    real = shutil.disk_usage
    monkeypatch.setattr(shutil, 'disk_usage', lambda p: real(p)._replace(free=10))
    low = s.complete(None, None, None, chat(('user', 'Other. ' + TOOL + '\n\nReturn KEYB')))
    assert low['orchid'] == {'engine': 'native', 'reason': 'disk full', 'thread': low['orchid']['thread']}
    monkeypatch.setattr(shutil, 'disk_usage', real)
    from memory_gateway.specprefill.session_store import SessionStore

    def no_space(self, snapshot):
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(SessionStore, 'write', no_space)
    history = chat(('user', TOOL + '\n\nReturn KEYA'), ('assistant', reply(first)), ('user', 'Return KEYB'))
    failed = s.complete(None, None, None, history)
    assert failed['orchid']['reason'] == 'lean failure'  # Answered natively, not an error.
    monkeypatch.undo()
    later = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), ('assistant', reply(first)),
                                              ('user', 'Return KEYC')))
    assert reply(later) == '33333' and later['orchid']['thread'] == first['orchid']['thread']


WRITER = r'''
import sys
sys.path[:0] = [sys.argv[2], sys.argv[3]]
from test_spec_session import Student, Target, Tok, doc
from memory_gateway.specprefill.spec_session import SpecSession
s = SpecSession(Tok(), Student(), Target(), system='Use excerpts.', checkpoint=sys.argv[1])
i = 0
while True:
    s.add_source(f'f{i}.py', 1, doc({f'KEY{i % 26}': '12345'}, seed=i))
    s.answer(f'Return KEY{i % 26}')
    i += 1
'''


def test_a_process_killed_mid_write_leaves_a_readable_checkpoint(tmp_path):
    from test_spec_session import Student, Target, Tok

    from memory_gateway.specprefill.spec_session import SpecSession
    tests, repo = str(Path(__file__).parent), str(Path(__file__).parent.parent)
    for trial in range(3):
        path = tmp_path / f'k{trial}.sqlite3'
        child = subprocess.Popen([sys.executable, '-c', WRITER, str(path), tests, repo])
        time.sleep(1.0 + 0.4 * trial)
        os.kill(child.pid, signal.SIGKILL)
        child.wait()
        restored = SpecSession.restore(path, Tok(), Student(), Target())
        assert restored.turn >= 1 and len(restored.sources) >= restored.turn
        assert SpecSession.restore(path, Tok(), Student(), Target()).snapshot() == restored.snapshot()


def test_state_directory_is_private(tmp_path):
    s, _ = service(tmp_path)
    assert stat.S_IMODE(os.stat(s.directory).st_mode) == 0o700
    s.close()


def test_admin_command_line(tmp_path, monkeypatch, capsys):
    import memory_gateway.specprefill.admin as admin
    s, _ = service(tmp_path)
    first = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    with TestClient(app_for(tmp_path, s)) as client:
        monkeypatch.setattr(admin.httpx, 'Client', lambda base_url, timeout: client)
        admin.main(['list'])
        assert first['orchid']['thread'][:16] in capsys.readouterr().out
        admin.main(['export', first['orchid']['thread'], '-o', str(tmp_path / 'out.jsonl')])
        assert json.loads((tmp_path / 'out.jsonl').read_text().splitlines()[0])['thread'] == first['orchid']['thread']
        admin.main(['storage'])
        assert '"archive_bytes"' in capsys.readouterr().out
        admin.main(['delete', first['orchid']['thread'], '--yes'])
        admin.main(['list'])
        assert 'no threads' in capsys.readouterr().out
