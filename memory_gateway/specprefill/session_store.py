"""Incremental SQLite checkpoints for SpecSession: one file per thread.

The JSON checkpoint rewrote the whole session after every turn (3.4 MB at a
250K-token thread). Here a turn's commit is one transaction that adds what is
new:
- sources are inserted once (they never change);
- the student and target token lists grow by appended chunks, and are
  rewritten only when they stop being append-only (a target rebuild, a student
  rollout, or a rolled-back turn);
- the small fields that change every turn replace one row.

WAL with synchronous=FULL, so a power loss leaves either the previous turn or
this one. `read()` returns the same snapshot dict as the JSON format, so both
restore through one path.
"""
from __future__ import annotations

import array
import json
import sqlite3
from pathlib import Path

LISTS = (('student_ids', 'student_rollouts'), ('target_ids', 'target_generation'))
TAIL = 64  # Tokens before the append point compared on each save (a cheap guard).


def _blob(ids):
    return array.array('i', ids).tobytes()


def _ids(blob):
    values = array.array('i')
    values.frombytes(blob)
    return values.tolist()


class SessionStore:
    def __init__(self, path):
        self.path = Path(path)
        self.db = None
        self.lists = {}  # name -> (stored length, marker, stored tail)

    def _open(self):
        if self.db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS sources(idx INTEGER PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tokens(name TEXT NOT NULL, seq INTEGER NOT NULL, ids BLOB NOT NULL,
                    PRIMARY KEY(name, seq));
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
        return self.db

    def write(self, snapshot):
        """Commit a SpecSession.snapshot(): only what changed since the last write."""
        db = self._open()
        lists = {}
        with db:
            stored = db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
            db.executemany('INSERT INTO sources VALUES (?,?)',
                           [(i, json.dumps(s, separators=(',', ':')))
                            for i, s in enumerate(snapshot['sources']) if i >= stored])
            for name, marker_name in LISTS:
                ids, marker = snapshot[name], snapshot[marker_name]
                length, old_marker, tail = self.lists.get(name, (0, None, []))
                appendable = (old_marker == marker and length <= len(ids)
                              and ids[max(0, length - TAIL):length] == tail)
                if not appendable:
                    db.execute('DELETE FROM tokens WHERE name=?', (name,))
                    length = 0
                if len(ids) > length:
                    seq = db.execute('SELECT COALESCE(MAX(seq)+1, 0) FROM tokens WHERE name=?', (name,)).fetchone()[0]
                    db.execute('INSERT INTO tokens VALUES (?,?,?)', (name, seq, _blob(ids[length:])))
                lists[name] = (len(ids), marker, ids[max(0, len(ids) - TAIL):])
            small = {k: v for k, v in snapshot.items() if k not in ('sources', 'student_ids', 'target_ids')}
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',
                       ('snapshot', json.dumps(small, separators=(',', ':'))))
        self.lists.update(lists)  # Only once the transaction committed.

    def read(self):
        """The snapshot dict last written (KeyError if there is none)."""
        db = self._open()
        row = db.execute("SELECT value FROM state WHERE key='snapshot'").fetchone()
        if row is None:
            raise KeyError('Empty session store')
        snapshot = json.loads(row[0])
        snapshot['sources'] = [json.loads(r[0]) for r in db.execute('SELECT record FROM sources ORDER BY idx')]
        for name, marker_name in LISTS:
            ids = []
            for (blob,) in db.execute('SELECT ids FROM tokens WHERE name=? ORDER BY seq', (name,)):
                ids += _ids(blob)
            snapshot[name] = ids
            self.lists[name] = (len(ids), snapshot[marker_name], ids[max(0, len(ids) - TAIL):])
        return snapshot

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
