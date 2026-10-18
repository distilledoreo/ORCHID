"""Derived, scoped archive search. Raw immutable sources remain authoritative.

SQLite FTS/symbol postings are built at ingestion. Optional embeddings are
computed once per chunk and compared as vectors, never by rerunning the student
on the whole archive. No model downloads or remote endpoints are used.
"""
from __future__ import annotations

import ast
import bisect
import hashlib
import heapq
import json
import math
import re
import sqlite3
import struct
from collections import defaultdict
from dataclasses import asdict, dataclass

_WORD = re.compile(r'\w+', re.UNICODE)
_CAMEL = re.compile(r'[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]?[a-z]+|\d+')
_STOP = set('a an and are as at be by can do does find for from how i in is it me of on or '
            'please return show tell that the this to use value what where which with would'.split())
_INDEX_VERSION = 'archive-search-v1'
# Explicit mentions bind before fuzzy ranking. A bare version or symbol never
# selects sources alone; a path mention is required, and version hints that
# eliminate every path candidate fail closed to no binding.
_PATH_HINT = re.compile(r'[\w\-.]+(?:/[\w\-.]+)+\.\w+|\b[\w\-.]+\.(?:py|txt|md|json|toml|cfg|ini)\b')
_VERSION_HINT = re.compile(r'\brevision\s+(\d+)|\bversions?\s*[=:\s]+\s*(\d+)|\bv(\d+)\b', re.IGNORECASE)
_PINNED_LIMIT = 64


def terms(text):
    result = []
    for word in _WORD.findall(text):
        result.append(word.casefold())
        result.extend(p.casefold() for part in word.split('_') for p in _CAMEL.findall(part))
    return list(dict.fromkeys(w for w in result if w and w not in _STOP))[:64]


def _vector(value):
    row = [float(v) for v in value]
    if not row or any(not math.isfinite(v) for v in row):
        raise ValueError('Invalid archive embedding')
    norm = math.sqrt(sum(v * v for v in row))
    if norm == 0:
        raise ValueError('Zero archive embedding')
    return [v / norm for v in row]


@dataclass(frozen=True)
class SearchHit:
    handle: str
    score: float
    reasons: tuple[str, ...]


class ArchiveSearch:
    def __init__(self, archive, *, path=None, embedder=None, embedding_id=None):
        if embedder is not None and not embedding_id:
            raise ValueError('Semantic retrieval requires a stable embedding identity')
        self.archive, self.embedder = archive, embedder
        self.embedding_id = embedding_id
        # Sessions serialize access but may run successive turns on different
        # executor threads. This connection is not independently concurrent.
        self.db = sqlite3.connect(str(path) if path else ':memory:', check_same_thread=False)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA cache_size=-2048')
        self.db.execute('PRAGMA mmap_size=0')
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sources(sid TEXT PRIMARY KEY, signature TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks(handle TEXT PRIMARY KEY, sid TEXT NOT NULL,
                idx INTEGER NOT NULL, body TEXT NOT NULL, path TEXT NOT NULL, cue TEXT NOT NULL,
                FOREIGN KEY(sid) REFERENCES sources(sid) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS chunks_source ON chunks(sid, idx);
            CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(handle UNINDEXED, body, path, cue);
            CREATE TABLE IF NOT EXISTS symbols(term TEXT NOT NULL, handle TEXT NOT NULL,
                PRIMARY KEY(term,handle), FOREIGN KEY(handle) REFERENCES chunks(handle) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS definitions(symbol TEXT NOT NULL, handle TEXT NOT NULL,
                PRIMARY KEY(symbol,handle), FOREIGN KEY(handle) REFERENCES chunks(handle) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS definition_units(symbol TEXT NOT NULL, handle TEXT NOT NULL,
                PRIMARY KEY(symbol,handle), FOREIGN KEY(handle) REFERENCES chunks(handle) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS dependencies(handle TEXT NOT NULL, symbol TEXT NOT NULL,
                PRIMARY KEY(handle,symbol), FOREIGN KEY(handle) REFERENCES chunks(handle) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS vectors(handle TEXT PRIMARY KEY, model TEXT NOT NULL,
                value BLOB NOT NULL, dimension INTEGER NOT NULL,
                FOREIGN KEY(handle) REFERENCES chunks(handle) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS index_flags(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TEMP TABLE scope(handle TEXT PRIMARY KEY);
        ''')
        self.last = {}
        self._units_done = set()

    def close(self):
        self.db.close()

    def sync(self, cues):
        """Reconcile a restart against the authoritative checkpoint; reuse valid rows."""
        with self.db:
            after = ''
            while True:
                batch = self.db.execute('SELECT sid FROM sources WHERE sid>? ORDER BY sid LIMIT 128', (after,)).fetchall()
                if not batch:
                    break
                for (sid,) in batch:
                    if sid not in self.archive.sources:
                        self._delete(sid)
                after = batch[-1][0]
            for sid in self.archive.sources:
                self.add_source(sid, cues.get(sid, ''))

    def _delete(self, sid):
        self.db.execute('DELETE FROM search_fts WHERE handle IN (SELECT handle FROM chunks WHERE sid=?)', (sid,))
        self.db.execute('DELETE FROM sources WHERE sid=?', (sid,))

    def add_source(self, sid, cue=''):
        source = self.archive.sources[sid]
        signature = hashlib.sha256(json.dumps([_INDEX_VERSION, source['sha256'], source['path'], cue]).encode()).hexdigest()
        old = self.db.execute('SELECT signature FROM sources WHERE sid=?', (sid,)).fetchone()
        with self.db:
            if old is None or old[0] != signature:
                self._delete(sid)
                self.db.execute('INSERT INTO sources VALUES (?,?)', (sid, signature))
                handles = source['chunks']
                for i, h in enumerate(handles):
                    chunk = self.archive.chunks[h]
                    self.db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?)',
                                    (h, sid, i, chunk.text, source['path'], cue))
                    # Include short overlap for words split at archive chunk boundaries.
                    before = self.archive.chunks[handles[i-1]].text[-64:] if i else ''
                    after = self.archive.chunks[handles[i+1]].text[:64] if i+1 < len(handles) else ''
                    view = before + chunk.text + after
                    self.db.execute('INSERT INTO search_fts VALUES (?,?,?,?)',
                        (h, view + '\n' + ' '.join(terms(view)), source['path'], cue))
                self._symbols_and_dependencies(sid)
            else:
                self._backfill_units(sid)
            if self.embedder is not None:
                missing = [r[0] for r in self.db.execute('''SELECT c.handle FROM chunks c
                    LEFT JOIN vectors v ON v.handle=c.handle
                    WHERE c.sid=? AND (v.handle IS NULL OR v.model != ?) ORDER BY c.idx''',
                    (sid, self.embedding_id))]
                for at in range(0, len(missing), 32):
                    batch = missing[at:at+32]
                    texts = [self._embedding_text(h) for h in batch]
                    rows = list(self.embedder.embed(texts))
                    if len(rows) != len(batch):
                        raise ValueError('Archive embedding row count mismatch')
                    for h, row in zip(batch, rows, strict=True):
                        values = _vector(row)
                        dimension = len(values)
                        other = self.db.execute('SELECT dimension FROM vectors WHERE model=? LIMIT 1',
                                                (self.embedding_id,)).fetchone()
                        if other and other[0] != dimension:
                            raise ValueError('Archive embedding dimension changed')
                        self.db.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)',
                            (h, self.embedding_id, struct.pack(f'<{dimension}f', *values), dimension))

    def _embedding_text(self, h):
        c = self.archive.chunks[h]
        s = self.archive.sources[c.source]
        handles = s['chunks']
        group = handles[max(0, c.index-1):c.index+2]
        return s['path'] + '\n' + ''.join(self.archive.chunks[x].text for x in group)

    def _def_groups(self, sid):
        """Yield (name, body chunk handles) for each defined function or class."""
        source = self.archive.sources[sid]
        if not source['path'].endswith('.py'):
            return
        text, handles = source['text'], source['chunks']
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return
        byte_lines, offset = [], 0
        for line in text.splitlines(keepends=True):
            byte_lines.append(offset)
            offset += len(line.encode())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            start = byte_lines[node.lineno-1] + node.col_offset
            stop = byte_lines[node.end_lineno-1] + node.end_col_offset
            yield node.name, [x for x in handles if self.archive.chunks[x].byte_start < stop
                              and self.archive.chunks[x].byte_end > start]

    def _backfill_units(self, sid):
        """Complete definition units for indexes built before they existed.

        Existing chunk, symbol, definition, dependency, and vector rows are
        untouched; only the new unit table gains rows for this source.
        """
        if sid in self._units_done:
            return
        self._units_done.add(sid)
        if self.db.execute('SELECT 1 FROM definition_units WHERE handle IN '
                           '(SELECT handle FROM chunks WHERE sid=?) LIMIT 1', (sid,)).fetchone():
            return
        with self.db:
            for name, group in self._def_groups(sid):
                self.db.executemany('INSERT OR IGNORE INTO definition_units VALUES (?,?)',
                                    ((name.casefold(), x) for x in group))

    def _symbols_and_dependencies(self, sid):
        source = self.archive.sources[sid]
        text = source['text']
        handles = source['chunks']
        ends = [self.archive.chunks[h].char_end for h in handles]
        for match in _WORD.finditer(text):
            if match.group(0)[0].isdigit():
                continue
            at = bisect.bisect_right(ends, match.start())
            while at < len(handles) and self.archive.chunks[handles[at]].char_start < match.end():
                self.db.execute('INSERT OR IGNORE INTO symbols VALUES (?,?)',
                                (match.group(0).casefold(), handles[at]))
                at += 1
        if not source['path'].endswith('.py'):
            self._units_done.add(sid)
            return
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            self._units_done.add(sid)
            return
        byte_lines, offset = [], 0
        for line in text.splitlines(keepends=True):
            byte_lines.append(offset)
            offset += len(line.encode())
        byte_ends = [self.archive.chunks[h].byte_end for h in handles]
        for node in ast.walk(tree):
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names = [node.id]
            if not names:
                continue
            start = byte_lines[node.lineno-1] + node.col_offset
            at = min(bisect.bisect_right(byte_ends, start), len(handles)-1)
            h = handles[at]
            for name in names:
                self.db.execute('INSERT OR IGNORE INTO definitions VALUES (?,?)', (name.casefold(), h))
            # Associate each function body chunk with the names it reads.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stop = byte_lines[node.end_lineno-1] + node.end_col_offset
                group = [x for x in handles if self.archive.chunks[x].byte_start < stop
                         and self.archive.chunks[x].byte_end > start]
                for name in names:
                    self.db.executemany('INSERT OR IGNORE INTO definition_units VALUES (?,?)',
                                        ((name.casefold(), x) for x in group))
                loads = {n.id.casefold() for n in ast.walk(node)
                         if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
                self.db.executemany('INSERT OR IGNORE INTO dependencies VALUES (?,?)',
                                    ((x, name) for x in group for name in loads))
        self._units_done.add(sid)

    def unit_group(self, handle):
        """All chunk handles of the defined function or class containing a chunk.

        Units keep a definition and its return value together through ranking
        and budget admission. Chunks outside any definition are their own unit.
        """
        rows = self.db.execute('SELECT c.sid FROM chunks c WHERE c.handle=?', (handle,)).fetchone()
        if rows is None:
            raise ValueError('Unknown archive chunk')
        siblings = self.db.execute('''SELECT u.handle FROM definition_units u
            JOIN chunks c ON c.handle=u.handle
            WHERE u.symbol IN (SELECT symbol FROM definition_units WHERE handle=?)
            AND c.sid=? ORDER BY c.idx''', (handle, rows[0])).fetchall()
        return [h for (h,) in siblings] or [handle]

    def unit_blocks(self, handles):
        """Partition handles into definition units, preserving first-seen order."""
        order = list(dict.fromkeys(handles))
        members = set(order)
        blocks, assigned = [], set()
        for h in order:
            if h in assigned:
                continue
            block = frozenset([u for u in self.unit_group(h) if u in members])
            blocks.append(block or frozenset([h]))
            assigned |= blocks[-1]
        return blocks

    def bind_references(self, question):
        """Pin chunks for explicitly mentioned path, version, and symbol triples.

        A path-like mention must match a known archived source; version hints
        only filter those path candidates. Symbols narrow the pin to complete
        definition units. Anything less explicit returns no binding, leaving
        the ordinary fuzzy ranking to decide.
        """
        if not isinstance(question, str) or not question.strip():
            return []
        mentioned = {p for p in _PATH_HINT.findall(question)}
        if not mentioned:
            return []
        versions = {int(v) for group in _VERSION_HINT.findall(question) for v in group if v}
        matches = [(sid, s) for sid in self.archive.sources for s in [self.archive.sources[sid]]
                   if any(s['path'] == p or s['path'].endswith('/' + p) for p in mentioned)]
        if versions:
            matches = [(sid, s) for sid, s in matches if s['version'] in versions]
            if not matches:
                return []
        defined = {row[0] for row in self.db.execute('SELECT DISTINCT symbol FROM definitions')}
        symbols = [t for t in terms(question) if t in defined]
        pinned = []
        for sid, source in sorted(matches, key=lambda item: (item[1]['path'], item[1]['version'])):
            chunks = list(source['chunks'])
            if symbols:
                wanted = set()
                for symbol in symbols:
                    wanted.update(h for (h,) in self.db.execute(
                        '''SELECT u.handle FROM definition_units u JOIN chunks c ON c.handle=u.handle
                           WHERE u.symbol=? AND c.sid=? ORDER BY c.idx''', (symbol, sid)))
                chunks = [h for h in chunks if h in wanted]
            pinned.extend(chunks)
            if len(pinned) >= _PINNED_LIMIT:
                break
        return pinned[:_PINNED_LIMIT]

    def search(self, query, handles, *, limit=64, neighbors=1, dependencies=True, exclude=(), pinned=()):
        if not isinstance(query, str) or not query.strip() or len(query) > 16384:
            raise ValueError('Search needs a nonempty bounded question')
        if type(limit) is not int or not 1 <= limit <= 512 or type(neighbors) is not int or not 0 <= neighbors <= 4:
            raise ValueError('Invalid archive retrieval limits')
        # Scope contains exact allowed handles, including ranges inside manifests.
        self.db.execute('DELETE FROM scope')
        self.db.executemany('INSERT OR IGNORE INTO scope VALUES (?)', ((h,) for h in handles))
        excluded = set(exclude)
        self.db.executemany('DELETE FROM scope WHERE handle=?', ((h,) for h in excluded))
        valid = {row[0] for row in self.db.execute('SELECT handle FROM scope')}
        ordered_pins = [h for h in dict.fromkeys(pinned) if h in valid]
        query_terms = terms(query)
        scores, reasons = defaultdict(float), defaultdict(set)
        branch_limit = min(512, max(limit * 4, 32))
        if query_terms:
            placeholders = ','.join('?' for _ in query_terms)
            exact = self.db.execute(f'''SELECT s.handle, COUNT(*) AS n FROM symbols s
                JOIN scope x ON x.handle=s.handle WHERE s.term IN ({placeholders})
                GROUP BY s.handle ORDER BY n DESC,s.handle LIMIT ?''', (*query_terms, branch_limit)).fetchall()
            for rank, (h, _) in enumerate(exact, 1):
                scores[h] += 2 / (60 + rank)
                reasons[h].add('symbol')
            # Quote every extracted term; user text never becomes FTS operators.
            match = ' OR '.join('"' + t.replace('"', '""') + '"' for t in query_terms)
            rows = self.db.execute('''SELECT f.handle FROM search_fts f JOIN scope x ON x.handle=f.handle
                WHERE search_fts MATCH ? ORDER BY bm25(search_fts,0,1,0.4,0.2),f.handle LIMIT ?''',
                (match, branch_limit)).fetchall()
            for rank, (h,) in enumerate(rows, 1):
                scores[h] += 1 / (60 + rank)
                reasons[h].add('text')
        if self.embedder is not None:
            rows = list(self.embedder.embed([query]))
            if len(rows) != 1:
                raise ValueError('Query embedding row count mismatch')
            vector = _vector(rows[0])
            def similarities():
                for h, blob, dim in self.db.execute('''SELECT v.handle,v.value,v.dimension FROM vectors v
                        JOIN scope x ON x.handle=v.handle WHERE v.model=?''', (self.embedding_id,)):
                    if dim != len(vector):
                        raise ValueError('Query embedding dimension mismatch')
                    value = sum(a*b for a,b in zip(vector, struct.unpack(f'<{dim}f', blob), strict=True))
                    if value > 0:
                        yield value, h
            dense = heapq.nsmallest(branch_limit, similarities(), key=lambda x: (-x[0], x[1]))
            for rank, (_, h) in enumerate(dense, 1):
                scores[h] += 1 / (60 + rank)
                reasons[h].add('semantic')
        ranked = sorted(scores, key=lambda h: (-scores[h], h))[:limit]
        result = []
        emitted = set()

        def emit(h, score, reason):
            if h not in emitted and len(result) < limit:
                emitted.add(h)
                result.append(SearchHit(h, score, tuple(sorted(reason))))

        # Explicit pins lead with their complete definition units, so a
        # definition and its return value cannot be split by ranking or budget.
        for h in ordered_pins:
            emit(h, scores.get(h, 0.0), {'pinned'})
            for sibling in self.unit_group(h):
                if sibling in valid:
                    emit(sibling, scores.get(sibling, 0.0), {'pinned', 'unit'})
        # Every direct hit precedes surrounding context, which previously
        # displaced later direct hits from the bounded emission.
        for h in ranked:
            emit(h, scores[h], reasons[h])
        seeds = ranked[:max(1, limit // 2)]
        for h in seeds:
            c = self.archive.chunks[h]
            if dependencies:
                related = self.db.execute('''SELECT DISTINCT d.handle FROM dependencies e
                    JOIN definitions d ON d.symbol=e.symbol JOIN scope x ON x.handle=d.handle
                    WHERE e.handle=? ORDER BY d.handle LIMIT ?''', (h, limit)).fetchall()
                for (other,) in related:
                    emit(other, scores[h] / 2, {'dependency'})
            if neighbors:
                related = self.db.execute('''SELECT c.handle FROM chunks c JOIN scope x ON x.handle=c.handle
                    WHERE c.sid=? AND c.idx BETWEEN ? AND ? ORDER BY ABS(c.idx-?),c.idx''',
                    (c.source, c.index-neighbors, c.index+neighbors, c.index)).fetchall()
                for (other,) in related:
                    emit(other, scores[h] / 2, {'neighbor'})
        self.last = {'scope_chunks': self.db.execute('SELECT COUNT(*) FROM scope').fetchone()[0],
                     'hits': [asdict(h) for h in result], 'semantic_enabled': self.embedder is not None,
                     'embedding_id': self.embedding_id,
                     'pinned': [h.handle for h in result if 'pinned' in h.reasons]}
        return result
