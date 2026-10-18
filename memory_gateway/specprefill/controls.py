"""User controls over the lean gateway's stored threads: list, export, delete, purge, quota.

Two kinds of data live in the state directory:
- archives: a thread's conversation (its session checkpoint, ledger rows and the
  gateway's event store). This is user data, and it is only removed when the
  user deletes the thread.
- caches: parked student states, Flash-Next session files (disk and /dev/shm).
  They are disposable, held within their own byte budgets, and safe to purge.

Deletion is complete: the session file, both caches, the hot file, the ledger
rows and the event store's thread, with SQLite's secure_delete so freed pages
are overwritten.
"""
from __future__ import annotations

import json
import sqlite3


def _size(paths):
    return sum(p.stat().st_size for p in paths if p.exists())


def archive_paths(service, key):
    base = service._session_path(key)
    return [base, base.with_name(base.name + '-wal'), base.with_name(base.name + '-shm'),
            base.with_suffix('.json'), base.with_name(base.stem + '.json.migrated')]


def cache_paths(service, key):
    return [service.directory / 'student-cache' / f'{key}.pt', service._target_file(key), service._target_hot(key)]


def _turns(service, key):
    path = service._session_path(key)
    if not path.exists():
        return 0
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        row = db.execute("SELECT value FROM state WHERE key='snapshot'").fetchone()
        return json.loads(row[0]).get('turn', 0) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        db.close()


def storage(service):
    """Bytes held, by kind, against the quota."""
    directory = service.directory
    archives = sum(_size(archive_paths(service, key)) for key in _keys(service))
    archives += _size([directory / 'gateway.sqlite3', directory / 'gateway.sqlite3-wal'])
    caches = sum(_size(list(d.glob('*'))) for d in (directory / 'student-cache', directory / 'target-cache')
                 if d.exists())
    hot = service._target_hot('x').parent
    caches += _size(list(hot.glob('*.bin'))) if hot.exists() else 0
    quota = service.options.state_quota_mib * 2**20
    return {'archive_bytes': archives, 'cache_bytes': caches, 'quota_bytes': quota or None,
            'warning': bool(quota) and archives >= 0.8 * quota, 'over_quota': bool(quota) and archives >= quota}


def _keys(service):
    return [row[0] for row in service.db.execute('SELECT key FROM sessions')]


def list_threads(service, store=None):
    rows = []
    for key, used_at in service.db.execute('SELECT key,used_at FROM sessions ORDER BY used_at DESC'):
        preview = None
        if store is not None:
            first = next((e for e in store.list_events('specprefill-' + key) if e.get('role') == 'user'), None)
            preview = (first or {}).get('content', '')[:80] or None
        rows.append({'thread': key, 'turns': _turns(service, key), 'last_used': used_at,
                     'archive_bytes': _size(archive_paths(service, key)),
                     'cache_bytes': _size(cache_paths(service, key)), 'preview': preview})
    return rows


def export_thread(service, store, key):
    """JSON lines: a header, then every stored message of the thread in order."""
    if service.db.execute('SELECT 1 FROM sessions WHERE key=?', (key,)).fetchone() is None:
        raise KeyError(key)
    yield json.dumps({'thread': key, 'turns': _turns(service, key)}) + '\n'
    for event in store.list_events('specprefill-' + key):
        yield json.dumps({k: event.get(k) for k in ('sequence', 'created_at', 'event_type', 'role', 'content')},
                         ensure_ascii=False) + '\n'


def delete_thread(service, store, key):
    """Remove a thread completely. Returns what was removed."""
    with service.lock:
        if service.db.execute('SELECT 1 FROM sessions WHERE key=?', (key,)).fetchone() is None:
            raise KeyError(key)
        session = service.sessions.pop(key, None)
        if session is not None:
            session.close()
        if service.active_key == key and service.resources is not None:
            service.resources.selector.reset()
            service.active_key = None
        if service.target_owner == key:
            service.target_owner = None
        service.seen.discard(key)
        if service.parking is not None:
            service.parking.discard(key)
        removed = _size(archive_paths(service, key)) + _size(cache_paths(service, key))
        for path in archive_paths(service, key) + cache_paths(service, key):
            path.unlink(missing_ok=True)
        service.db.execute('PRAGMA secure_delete = ON')
        with service.db:
            for table in ('sessions', 'history_entries', 'completions'):
                service.db.execute(f'DELETE FROM {table} WHERE key=?', (key,))
        service.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        events = store.delete_thread('specprefill-' + key) if store is not None else 0
        return {'thread': key, 'bytes': removed, 'events': events}


def purge_caches(service):
    """Drop every parked state and session file (they rebuild on demand)."""
    with service.lock:
        removed = 0
        if service.parking is not None:
            service.parking.flush()
            service.parking.ram.clear()
        hot = service._target_hot('x').parent
        for directory in (service.directory / 'student-cache', service.directory / 'target-cache', hot):
            if directory.exists():
                for path in directory.iterdir():
                    if path.is_file():
                        removed += path.stat().st_size
                        path.unlink(missing_ok=True)
        return {'bytes': removed}
