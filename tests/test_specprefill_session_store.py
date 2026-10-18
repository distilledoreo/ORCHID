"""Incremental SQLite session checkpoints: equivalence, appends, rewrites, migration, crashes."""
import sqlite3

import pytest
from test_spec_session import Student, Target, Tok, doc

from memory_gateway.specprefill.spec_session import SpecLimits, SpecSession


def session(path, **limits):
    return SpecSession(Tok(), Student(), Target(), system='Use excerpts.', limits=SpecLimits(**limits),
                       checkpoint=path)


def reopen(path):
    return SpecSession.restore(path, Tok(), Student(), Target())


def test_restore_equals_the_live_session_and_turns_append(tmp_path):
    path = tmp_path / 't.sqlite3'
    s = session(path)
    sizes = []
    for i in range(4):
        s.add_source(f'f{i}.py', 1, doc({f'KEY{i}': f'{i}' * 5}, seed=3 + i))
        s.answer(f'Return KEY{i}')
        db = sqlite3.connect(path)
        sizes.append(db.execute("SELECT COUNT(*) FROM tokens WHERE name='target_ids'").fetchone()[0])
        db.close()
    assert sizes == sorted(sizes) and sizes[-1] > 1  # Appended chunks, not one rewritten blob.
    back = reopen(path)
    assert back.snapshot() == s.snapshot()
    assert Tok().decode(back.answer('Return KEY2')) == '22222'  # The restored session keeps working.
    assert reopen(path).snapshot() == back.snapshot()


def test_target_rebuilds_and_student_rollouts_rewrite_their_lists_correctly(tmp_path):
    path = tmp_path / 't.sqlite3'
    s = session(path, target_context=1400, student_context=12000, new_rate=0.1, recall_tokens=64,
                rebuild_turns=1, relative_floor=0.0)
    for i in range(6):
        s.add_source(f'f{i}.py', 1, doc({f'KEY{i}': f'{i}' * 5}, seed=3 + i))
        s.answer(f'Return KEY{i}')
        assert reopen(path).snapshot() == s.snapshot()
    assert s.target_generation >= 1 and s.student_rollouts >= 1


def test_a_failed_turn_rolls_back_and_the_next_save_is_still_consistent(tmp_path):
    path = tmp_path / 't.sqlite3'
    s = session(path)
    s.add_source('a.py', 1, doc({'KEYA': '11111'}))
    s.answer('Return KEYA')
    def boom(ids, maximum):
        raise RuntimeError('engine down')
    real, s.target = s.target, boom
    with pytest.raises(RuntimeError):
        s.answer('Return KEYA again')
    s.target = real
    s.answer('Return KEYA')
    assert reopen(path).snapshot() == s.snapshot()


def test_json_checkpoints_migrate_once_and_are_kept(tmp_path):
    legacy = tmp_path / 't.json'
    old = session(legacy)
    old.add_source('a.py', 1, doc({'KEYA': '11111'}))
    old.answer('Return KEYA')
    migrated = reopen(tmp_path / 't.sqlite3')
    assert migrated.snapshot() == old.snapshot()
    assert (tmp_path / 't.sqlite3').exists() and (tmp_path / 't.json.migrated').exists() and not legacy.exists()
    assert reopen(tmp_path / 't.sqlite3').snapshot() == old.snapshot()


def test_a_crash_inside_a_commit_leaves_the_previous_turn(tmp_path, monkeypatch):
    path = tmp_path / 't.sqlite3'
    s = session(path)
    s.add_source('a.py', 1, doc({'KEYA': '11111'}))
    s.answer('Return KEYA')
    s.add_source('b.py', 1, doc({'KEYB': '22222'}, seed=9))
    before = reopen(path).snapshot()  # The last commit before the crash.
    real = s._store.write

    def crash(snapshot):
        db = s._store._open()
        with pytest.raises(OSError):
            with db:
                db.execute('DELETE FROM tokens')  # Partway through ...
                raise OSError('power lost')       # ... the commit never happens.
    monkeypatch.setattr(s._store, 'write', crash)
    s.answer('Return KEYB')
    assert reopen(path).snapshot() == before
    monkeypatch.setattr(s._store, 'write', real)
    s.answer('Return KEYB')
    assert reopen(path).snapshot() == s.snapshot()
