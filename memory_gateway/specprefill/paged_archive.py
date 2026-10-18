"""Transactional raw archive with lazy records and a bounded Python payload cache.

The connection is owned/serialized by CompactingSession. All mutations, including
active session state, commit together. SQLite temp/page caches are bounded apart
from the OS filesystem cache. Caller-held objects and ingestion inputs are not
part of the retained payload cache budget.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import struct
import sys
import uuid
from collections import OrderedDict
from collections.abc import Mapping, MutableMapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

from .omission_archive import Chunk, OmissionArchive


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def seal(value, raw=b''):
    return hashlib.sha256(canonical(value).encode() + b'\0' + raw).hexdigest()


def footprint(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(footprint(k, seen) + footprint(v, seen) for k, v in value.items())
    elif isinstance(value, (tuple, list)):
        size += sum(footprint(v, seen) for v in value)
    elif isinstance(value, Chunk):
        size += footprint(value.__dict__, seen)
    return size


class PayloadCache:
    def __init__(self, budget):
        if type(budget) is not int or budget < 0:
            raise ValueError('Archive cache byte budget must be a nonnegative integer')
        self.budget, self.bytes, self.hits, self.loads = budget, 0, 0, 0
        self.values = OrderedDict()

    def get(self, key, load):
        if key in self.values:
            self.hits += 1
            self.values.move_to_end(key)
            return self.values[key][0]
        self.loads += 1
        value = load()
        cost = footprint(key) + footprint(value) + 128
        if cost <= self.budget:
            while self.values and self.bytes + cost > self.budget:
                _, (_, removed) = self.values.popitem(last=False)
                self.bytes -= removed
            self.values[key] = (value, cost)
            self.bytes += cost
        return value

    def clear(self):
        self.values.clear()
        self.bytes = 0


class SQLMap(MutableMapping):
    def __init__(self, store, kind):
        self.store, self.kind = store, kind

    def __len__(self):
        return self.store.db.execute(f'SELECT COUNT(*) FROM {self.kind}').fetchone()[0]

    def __iter__(self):
        for (key,) in self.store.db.execute(f'SELECT key FROM {self.kind} ORDER BY rowid'):
            yield key

    def __contains__(self, key):
        return self.store.db.execute(f'SELECT 1 FROM {self.kind} WHERE key=?', (key,)).fetchone() is not None

    def __getitem__(self, key):
        row = self.store.db.execute(f'SELECT value,checksum FROM {self.kind} WHERE key=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        value = json.loads(row[0])
        if seal(value) != row[1]:
            raise ValueError(f'Corrupt archive {self.kind} record')
        if self.kind == 'manifests':
            expected = 'A' + hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()
            if expected != key:
                raise ValueError('Archive manifest checksum mismatch')
            return tuple(value)
        return value

    def __setitem__(self, key, value):
        if self.kind == 'manifests':
            expected = 'A' + hashlib.sha256(json.dumps(list(value), separators=(',', ':')).encode()).hexdigest()
            if expected != key:
                raise ValueError('Archive manifest checksum mismatch')
            if key in self:
                if self[key] != tuple(value):
                    raise ValueError('Immutable manifest changed')
                return
            for ref in value:
                self.store.validate_reference(ref)
        self.store.db.execute(f'INSERT OR REPLACE INTO {self.kind} VALUES (?,?,?)',
                              (key, canonical(value), seal(value)))
        if self.kind == 'manifests':
            for ref in value:
                sid, lo, hi = (None, None, None) if ref.startswith('A') else self.store._range(ref)
                self.store.db.execute('INSERT INTO manifest_edges VALUES (?,?,?,?,?)', (key, ref, sid, lo, hi))

    def __delitem__(self, key):
        if self.kind == 'manifests':
            raise TypeError('Archive manifests are immutable')
        if self.store.db.execute(f'DELETE FROM {self.kind} WHERE key=?', (key,)).rowcount == 0:
            raise KeyError(key)


class EventLog(Sequence):
    def __init__(self, store, kind):
        self.store, self.kind = store, kind

    def __len__(self):
        return self.store.db.execute('SELECT COUNT(*) FROM events WHERE kind=?', (self.kind,)).fetchone()[0]

    def append(self, value):
        self.store.db.execute('INSERT INTO events(kind,value,checksum) VALUES (?,?,?)',
                              (self.kind, canonical(value), seal(value)))

    def _rows(self, order):
        for row in self.store.db.execute(f'SELECT value,checksum FROM events WHERE kind=? ORDER BY seq {order}', (self.kind,)):
            value = json.loads(row[0])
            if seal(value) != row[1]:
                raise ValueError('Corrupt archive event')
            yield value

    def __iter__(self):
        return self._rows('ASC')

    def __reversed__(self):
        return self._rows('DESC')

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self))) ]
        order, offset = ('DESC', -index-1) if index < 0 else ('ASC',index)
        row = self.store.db.execute(f'SELECT value,checksum FROM events WHERE kind=? ORDER BY seq {order} LIMIT 1 OFFSET ?',
                                    (self.kind, offset)).fetchone()
        if row is None:
            raise IndexError(index)
        value = json.loads(row[0])
        if seal(value) != row[1]:
            raise ValueError('Corrupt archive event')
        return value


class TokenIDs(Sequence):
    PAGE = 1024

    def __init__(self, store, sid, count):
        self.store, self.sid, self.count = store, sid, count

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(self.count))]
        if index < 0:
            index += self.count
        if not 0 <= index < self.count:
            raise IndexError(index)
        page = index // self.PAGE
        def load():
            self.store.reads['token_pages'] += 1
            row = self.store.db.execute('SELECT value,checksum FROM token_pages WHERE sid=? AND page=?',
                                       (self.sid, page)).fetchone()
            if row is None or hashlib.sha256(row[0]).hexdigest() != row[1]:
                raise ValueError('Corrupt archive token page')
            return row[0]
        blob = self.store.cache.get(('tokens', self.sid, page), load)
        return struct.unpack_from('<I', blob, index % self.PAGE * 4)[0]

    def __eq__(self, other):
        return isinstance(other, Sequence) and len(self) == len(other) and all(a == b for a, b in zip(self, other, strict=True))


class SourceRecord(Mapping):
    def __init__(self, store, sid):
        self.store, self.sid = store, sid
        self.meta = store.source_metadata(sid)

    def __iter__(self):
        yield from self.meta['record']
        yield from ('text', 'token_ids', 'chunks', 'occurrences')

    def __len__(self):
        return len(self.meta['record']) + 4

    def __getitem__(self, key):
        if key == 'text':
            def load():
                self.store.reads['source_bodies'] += 1
                row = self.store.db.execute('SELECT body FROM sources WHERE sid=?', (self.sid,)).fetchone()
                if row is None or hashlib.sha256(row[0]).hexdigest() != self.meta['record']['sha256']:
                    raise ValueError('Archive source checksum mismatch')
                return row[0].decode('utf-8')
            return self.store.cache.get(('body', self.sid), load)
        if key == 'token_ids':
            return TokenIDs(self.store, self.sid, self.meta['token_count'])
        if key == 'chunks':
            return [row[0] for row in self.store.db.execute('SELECT handle FROM chunks WHERE sid=? ORDER BY idx', (self.sid,))]
        if key == 'occurrences':
            return [json.loads(row[0]) for row in self.store.db.execute('SELECT value FROM occurrences WHERE sid=? ORDER BY rowid', (self.sid,))]
        return self.meta['record'][key]


class Records(Mapping):
    def __init__(self, store, kind):
        self.store, self.kind = store, kind

    def __len__(self):
        return self.store.db.execute(f'SELECT COUNT(*) FROM {self.kind}').fetchone()[0]

    def __iter__(self):
        query = 'SELECT sid FROM sources ORDER BY ordinal' if self.kind == 'sources' else 'SELECT handle FROM chunks ORDER BY source_ordinal,idx'
        for (key,) in self.store.db.execute(query):
            yield key

    def __contains__(self, key):
        field = 'sid' if self.kind == 'sources' else 'handle'
        return self.store.db.execute(f'SELECT 1 FROM {self.kind} WHERE {field}=?', (key,)).fetchone() is not None

    def __getitem__(self, key):
        if self.kind == 'sources':
            return SourceRecord(self.store, key)
        def load():
            self.store.reads['chunks'] += 1
            row = self.store.db.execute('''SELECT c.metadata,c.checksum,
                substr(s.body,c.byte_start+1,c.byte_length),c.sid,c.source_ordinal,c.idx,c.byte_start,c.byte_length FROM chunks c
                JOIN sources s ON s.sid=c.sid WHERE c.handle=?''', (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            meta, raw = json.loads(row[0]), row[2]
            if (seal(meta, raw) != row[1] or meta['handle'] != key
                    or (meta['source'], int(meta['source'][1:]), meta['index'], meta['byte_start'],
                        meta['byte_end']-meta['byte_start']) != tuple(row[3:])):
                raise ValueError('Archive chunk checksum mismatch')
            token_ids = self.store.sources[meta['source']]['token_ids'][meta['token_start']:meta['token_end']]
            if b''.join(self.store.tok.token_bytes(t) for t in token_ids) != raw:
                raise ValueError('Archive tokenizer identity changed')
            return Chunk(**meta, text=raw.decode('utf-8'))
        return self.store.cache.get(('chunk', key), load)


class Current(Mapping):
    def __init__(self, store):
        self.store = store

    def __iter__(self):
        return (row[0] for row in self.store.db.execute('SELECT path FROM sources GROUP BY path ORDER BY MIN(ordinal)'))

    def __len__(self):
        return self.store.db.execute('SELECT COUNT(DISTINCT path) FROM sources').fetchone()[0]

    def __getitem__(self, key):
        row = self.store.db.execute('SELECT sid FROM sources WHERE path=? ORDER BY version DESC LIMIT 1', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return row[0]


class Handles(Sequence):
    def __init__(self, store):
        self.store = store

    def __len__(self):
        return self.store.db.execute('SELECT COUNT(*) FROM request_handles').fetchone()[0]

    def __iter__(self):
        return (row[0] for row in self.store.db.execute('SELECT handle FROM request_handles ORDER BY rowid'))

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self))) ]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return self.store.db.execute('SELECT handle FROM request_handles ORDER BY rowid LIMIT 1 OFFSET ?', (index,)).fetchone()[0]


class PagedArchive(OmissionArchive):
    def __init__(self, path, tokenizer, *, cache_bytes=8 * 1024**2):
        super().__init__(tokenizer)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cache = PayloadCache(cache_bytes)
        self.reads = dict(source_bodies=0, chunks=0, token_pages=0, metadata=0)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA cache_size=-4096')
        self.db.execute('PRAGMA mmap_size=0')
        self.db.execute('PRAGMA temp_store=FILE')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError('Unsupported paged archive schema')
        if version == 0:
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(sid TEXT PRIMARY KEY,ordinal INTEGER UNIQUE NOT NULL,
                path TEXT NOT NULL,version INTEGER NOT NULL,body BLOB NOT NULL,metadata TEXT NOT NULL,
                checksum TEXT NOT NULL,UNIQUE(path,version));
            CREATE TABLE IF NOT EXISTS token_pages(sid TEXT NOT NULL,page INTEGER NOT NULL,value BLOB NOT NULL,
                checksum TEXT NOT NULL,PRIMARY KEY(sid,page),FOREIGN KEY(sid) REFERENCES sources(sid));
            CREATE TABLE IF NOT EXISTS chunks(handle TEXT PRIMARY KEY,sid TEXT NOT NULL,source_ordinal INTEGER NOT NULL,
                idx INTEGER NOT NULL,byte_start INTEGER NOT NULL,byte_length INTEGER NOT NULL,
                metadata TEXT NOT NULL,checksum TEXT NOT NULL,UNIQUE(sid,idx),FOREIGN KEY(sid) REFERENCES sources(sid));
            CREATE TABLE IF NOT EXISTS occurrences(sid TEXT NOT NULL,value TEXT NOT NULL,UNIQUE(sid,value));
            CREATE TABLE IF NOT EXISTS manifests(key TEXT PRIMARY KEY,value TEXT NOT NULL,checksum TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cues(key TEXT PRIMARY KEY,value TEXT NOT NULL,checksum TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS manifest_edges(parent TEXT NOT NULL,child TEXT NOT NULL,sid TEXT,lo INTEGER,hi INTEGER);
            CREATE INDEX IF NOT EXISTS manifest_parent ON manifest_edges(parent);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,kind TEXT NOT NULL,value TEXT NOT NULL,checksum TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_kind ON events(kind,seq);
            CREATE TABLE IF NOT EXISTS session_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),value TEXT NOT NULL,checksum TEXT NOT NULL);
            CREATE TEMP TABLE IF NOT EXISTS request_handles(handle TEXT PRIMARY KEY);
            CREATE TEMP TABLE IF NOT EXISTS roots(ref TEXT PRIMARY KEY);
            CREATE TEMP TABLE IF NOT EXISTS coverage(handle TEXT PRIMARY KEY);
            CREATE TEMP TABLE IF NOT EXISTS ref_membership(handle TEXT PRIMARY KEY);
            PRAGMA user_version=1;
        ''')
        self.db.executescript('''
            CREATE TEMP TABLE IF NOT EXISTS request_handles(handle TEXT PRIMARY KEY);
            CREATE TEMP TABLE IF NOT EXISTS roots(ref TEXT PRIMARY KEY);
            CREATE TEMP TABLE IF NOT EXISTS coverage(handle TEXT PRIMARY KEY);
            CREATE TEMP TABLE IF NOT EXISTS ref_membership(handle TEXT PRIMARY KEY);
        ''')
        if version == 0:
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('archive_id',?)", (str(uuid.uuid4()),))
        self.archive_id = self.db.execute("SELECT value FROM metadata WHERE key='archive_id'").fetchone()[0]
        self.sources, self.chunks = Records(self, 'sources'), Records(self, 'chunks')
        self.current = Current(self)
        self.manifests, self.cues = SQLMap(self, 'manifests'), SQLMap(self, 'cues')
        self.events, self.session_events, self.messages = EventLog(self, 'archive'), EventLog(self, 'session'), EventLog(self, 'message')

    def source_metadata(self, sid):
        def load():
            self.reads['metadata'] += 1
            row = self.db.execute('SELECT metadata,checksum,path,version,ordinal FROM sources WHERE sid=?', (sid,)).fetchone()
            if row is None:
                raise KeyError(sid)
            value = json.loads(row[0])
            if (seal(value) != row[1] or value['record']['id'] != sid
                    or (value['record']['path'],value['record']['version']) != (row[2],row[3])
                    or sid != f'S{row[4]}'
                    or value['record']['original_end']-value['record']['original_start'] != value['token_count']):
                raise ValueError('Archive source metadata checksum mismatch')
            return value
        return self.cache.get(('metadata', sid), load)

    def source_id(self, path, version):
        row = self.db.execute('SELECT sid FROM sources WHERE path=? AND version=?', (path, version)).fetchone()
        return row[0] if row else None

    @contextmanager
    def savepoint(self):
        if not self.db.in_transaction:
            self.db.execute('BEGIN')
        self.db.execute('SAVEPOINT archive_mutation')
        try:
            yield
            self.db.execute('RELEASE archive_mutation')
        except BaseException:
            self.db.execute('ROLLBACK TO archive_mutation')
            self.db.execute('RELEASE archive_mutation')
            self.cache.clear()
            raise

    def add(self, path, version, text, original_start, turn):
        # Build/validate only this bounded ingestion item, never the whole archive.
        if (type(version) is not int or version < 1 or type(original_start) is not int
                or original_start < 0 or not isinstance(text, str) or not isinstance(path, str)):
            raise ValueError('Invalid source provenance')
        prior = self.source_id(path, version)
        if prior:
            s = self.sources[prior]
            if s['sha256'] != hashlib.sha256(text.encode()).hexdigest():
                raise ValueError('An immutable source version cannot change content')
            occurrence = {'original_start': original_start, 'original_end': original_start+len(s['token_ids']), 'turn': turn}
            self.db.execute('INSERT OR IGNORE INTO occurrences VALUES (?,?)', (prior, canonical(occurrence)))
            self.events.append({'event':'source_reencounter','source':prior, **occurrence})
            return prior
        temporary = OmissionArchive(self.tok)
        temporary.add(path, version, text, original_start, turn)
        ordinal = self.db.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM sources').fetchone()[0]
        sid = f'S{ordinal}'
        source = temporary.sources['S1']
        old = self.current.get(path)
        record = {k:v for k,v in source.items() if k not in ('text','token_ids','chunks','occurrences')}
        record['id'] = sid
        record['supersedes'] = old if old and version > self.sources[old]['version'] else None
        meta = {'record':record,'token_count':len(source['token_ids'])}
        with self.savepoint():
            self.db.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?)',
                (sid, ordinal, path, version, text.encode(), canonical(meta), seal(meta)))
            ids = source['token_ids']
            for at in range(0, len(ids), TokenIDs.PAGE):
                page = ids[at:at+TokenIDs.PAGE]
                raw = struct.pack(f'<{len(page)}I', *page)
                self.db.execute('INSERT INTO token_pages VALUES (?,?,?,?)',
                                (sid, at//TokenIDs.PAGE, raw, hashlib.sha256(raw).hexdigest()))
            for old_chunk in temporary.chunks.values():
                c = replace(old_chunk, source=sid, handle=f'{sid}:{old_chunk.index}')
                data = {k:v for k,v in asdict(c).items() if k != 'text'}
                self.db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)',
                    (c.handle, sid, ordinal, c.index, c.byte_start, c.byte_end-c.byte_start,
                     canonical(data), seal(data, c.text.encode())))
            for occurrence in source['occurrences']:
                self.db.execute('INSERT INTO occurrences VALUES (?,?)', (sid, canonical(occurrence)))
            self.events.append({'event':'archive','source':sid,'turn':turn})
        return sid

    def _range(self, handle):
        m = re.fullmatch(r'(S[1-9]\d*)(?::(\d+)(?:-(\d+))?)?', handle)
        if not m or m[1] not in self.sources:
            raise ValueError('Unknown archive handle')
        count = self.db.execute('SELECT COUNT(*) FROM chunks WHERE sid=?', (m[1],)).fetchone()[0]
        lo, hi = (0, count-1) if m[2] is None else (int(m[2]), int(m[3] or m[2]))
        if m[2] is not None and not 0 <= lo <= hi < count:
            raise ValueError('Archive chunk range is out of bounds')
        return m[1], lo, hi

    def resolve(self, handle):
        sid, lo, hi = self._range(handle)
        return [row[0] for row in self.db.execute('SELECT handle FROM chunks WHERE sid=? AND idx BETWEEN ? AND ? ORDER BY idx', (sid,lo,hi))]

    def validate_reference(self, ref):
        if ref.startswith('A'):
            if ref not in self.manifests:
                raise ValueError('Unknown archive manifest')
            self.manifests[ref]
        else:
            self._range(ref)

    def iter_references(self, references):
        self.db.execute('DELETE FROM roots')
        for ref in references:
            self.validate_reference(ref)
            self.db.execute('INSERT OR IGNORE INTO roots VALUES (?)', (ref,))
        # Check every visited manifest/edge against its content-addressed record.
        cte = '''WITH RECURSIVE refs(ref) AS (SELECT ref FROM roots UNION
                 SELECT e.child FROM manifest_edges e JOIN refs r ON e.parent=r.ref)'''
        for (key,) in self.db.execute(cte + ' SELECT m.key FROM manifests m JOIN refs r ON r.ref=m.key'):
            refs = self.manifests[key]
            edges = tuple(row[0] for row in self.db.execute('SELECT child FROM manifest_edges WHERE parent=? ORDER BY rowid', (key,)))
            if refs != edges:
                raise ValueError('Archive manifest edge mismatch')
        # Materialize membership on disk; leaf source ranges are small metadata.
        self.db.execute('DELETE FROM ref_membership')
        for (ref,) in self.db.execute(cte + ' SELECT ref FROM refs WHERE substr(ref,1,1)=\'S\''):
            sid, lo, hi = self._range(ref)
            self.db.execute('INSERT OR IGNORE INTO ref_membership SELECT handle FROM chunks WHERE sid=? AND idx BETWEEN ? AND ?', (sid,lo,hi))
        for (h,) in self.db.execute('SELECT m.handle FROM ref_membership m JOIN chunks c ON c.handle=m.handle ORDER BY c.source_ordinal,c.idx'):
            yield h

    def references_contain(self, refs, handles):
        missing = set(handles)
        for h in self.iter_references(refs):
            missing.discard(h)
            if not missing:
                return True
        return not missing

    def select_handles(self, references=None, *, exclude=()):
        self.db.execute('DELETE FROM request_handles')
        if references:
            self.db.executemany('INSERT OR IGNORE INTO request_handles VALUES (?)',
                                ((h,) for h in self.iter_references(references)))
        else:
            self.db.execute('INSERT INTO request_handles SELECT handle FROM chunks ORDER BY source_ordinal,idx')
        self.db.executemany('DELETE FROM request_handles WHERE handle=?', ((h,) for h in exclude))
        return Handles(self)

    def covers(self, references):
        self.db.execute('DELETE FROM coverage')
        self.db.executemany('INSERT OR IGNORE INTO coverage VALUES (?)', ((h,) for h in self.iter_references(references)))
        return self.db.execute('SELECT COUNT(*) FROM coverage').fetchone()[0] == len(self.chunks)

    def save_session(self, state):
        state = {**state, 'archive_id':self.archive_id,
                 'archive_runtime':{'cache_epoch':self.cache_epoch,'delivered':self.delivered,
                                    'placeholder_emitted':sorted(self.placeholder_emitted)}}
        self.db.execute('INSERT OR REPLACE INTO session_state VALUES (1,?,?)', (canonical(state), seal(state)))
        self.db.commit()

    def load_session(self):
        row = self.db.execute('SELECT value,checksum FROM session_state WHERE singleton=1').fetchone()
        if row is None:
            return None
        state = json.loads(row[0])
        if seal(state) != row[1] or state['archive_id'] != self.archive_id:
            raise ValueError('Session checkpoint checksum mismatch')
        runtime = state.get('archive_runtime', {})
        self.cache_epoch = runtime.get('cache_epoch',0) + 1
        self.delivered = runtime.get('delivered',{})
        self.placeholder_emitted = set(runtime.get('placeholder_emitted',[]))
        return state

    def close(self):
        self.db.close()  # Rolls back any uncheckpointed mutations.
        self.cache.clear()

    def to_dict(self):
        """Explicit compatibility export; normal paging/checkpoint paths never call this."""
        return {'schema':1, 'sources':{sid:{k:(list(v) if isinstance(v, TokenIDs) else v)
                    for k,v in self.sources[sid].items()} for sid in self.sources},
                'chunks':{h:asdict(self.chunks[h]) for h in self.chunks}, 'current':dict(self.current),
                'delivered':self.delivered,'cached':[], 'placeholder_emitted':sorted(self.placeholder_emitted),
                'cache_epoch':self.cache_epoch,'events':list(self.events),'messages':list(self.messages)}
