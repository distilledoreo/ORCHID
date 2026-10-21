"""Scoped recall over immutable normalized events, independent of hot memory."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from hashlib import sha256

import httpx

from .db import canonical_json
from .message_views import source_text

CHUNK_CHARS = 2048


def initialize_index(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS raw_event_documents (
            event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, thread_id TEXT NOT NULL,
            sequence INTEGER NOT NULL, role TEXT, text TEXT NOT NULL, source_hash TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS raw_event_scope ON raw_event_documents(project_id, thread_id, sequence);
        CREATE VIRTUAL TABLE IF NOT EXISTS raw_events_fts USING fts5(event_id UNINDEXED, text);
        CREATE TABLE IF NOT EXISTS raw_event_embeddings (
            event_id TEXT NOT NULL, view_start INTEGER NOT NULL, view_end INTEGER NOT NULL,
            index_id TEXT NOT NULL, vector_json TEXT NOT NULL,
            PRIMARY KEY(event_id, view_start, index_id));
    """)
    # event_id is UNINDEXED in the FTS table: a join on it scans the whole index once per
    # document (quadratic; 40K events took over 20 minutes at startup). NOT IN reads it once.
    connection.execute("""INSERT INTO raw_events_fts(event_id, text)
        SELECT d.event_id, d.text FROM raw_event_documents d
        WHERE d.event_id NOT IN (SELECT event_id FROM raw_events_fts)""")
    while True:
        rows = connection.execute("""SELECT e.* FROM events e
            LEFT JOIN raw_event_documents d ON d.event_id=e.id
            WHERE e.privacy_class='normal' AND d.event_id IS NULL LIMIT 256""").fetchall()
        if not rows:
            break
        for row in rows:
            index_event(connection, dict(row))


def index_event(connection, event):
    if event.get("privacy_class", "normal") != "normal":
        return
    text = source_text(event)
    exists = connection.execute("SELECT 1 FROM raw_event_documents WHERE event_id=?", (event["id"],)).fetchone()
    if exists:
        return
    connection.execute("INSERT INTO raw_event_documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (event["id"], event["project_id"], event["thread_id"], event["sequence"], event["role"],
                        text, sha256(text.encode()).hexdigest()))
    connection.execute("INSERT INTO raw_events_fts(event_id, text) VALUES (?, ?)", (event["id"], text))


def index_event_fail_open(connection, event):
    # Index failures cannot prevent an authoritative raw append.
    try:
        connection.execute("SAVEPOINT raw_index")
        index_event(connection, event)
        connection.execute("RELEASE raw_index")
    except Exception:
        try:
            connection.execute("ROLLBACK TO raw_index")
            connection.execute("RELEASE raw_index")
        except sqlite3.Error:
            pass


def excerpt(row, query="", start=None, limit=640):
    text = row["text"]
    if start is None:
        terms = re.findall(r"\w+", query.lower())[:32]
        offsets = [text.lower().find(term) for term in terms if term in text.lower()]
        start = max(0, min(offsets, default=0) - 100)
    end = min(len(text), start + limit)
    return {"event_id": row["event_id"], "sequence": row["sequence"], "role": row["role"],
            "source_view": "message-text-v1", "start": start, "end": end, "source_length": len(text),
            "excerpt": text[start:end]}


class RawRecall:
    def __init__(self, store, *, embeddings_url=None, embeddings_model=None, embeddings_api_key=None,
                 timeout=15.0, transport=None):
        self.store = store
        self.url = embeddings_url
        self.model = embeddings_model
        self.api_key = embeddings_api_key
        self.timeout = timeout
        self.transport = transport
        from .telemetry import endpoint_identity
        self.index_id = sha256(canonical_json([endpoint_identity(self.url or ""), self.model]).encode()).hexdigest()

    def _scope(self, project_id, thread_id):
        with self.store.connect() as connection:
            if connection.execute("SELECT 1 FROM threads WHERE id=? AND project_id=?",
                                  (thread_id, project_id)).fetchone() is None:
                raise ValueError("project/thread scope does not exist")

    def lexical(self, project_id, thread_id, query, k):
        self._scope(project_id, thread_id)
        if not isinstance(query, str) or not query.strip() or len(query) > 512:
            raise ValueError("query must contain 1-512 characters")
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 50:
            raise ValueError("k must be between 1 and 50")
        with self.store.connect() as connection:
            if query.startswith("event:"):
                rows = connection.execute("""SELECT d.* FROM raw_event_documents d JOIN events e ON e.id=d.event_id
                    WHERE d.project_id=? AND d.thread_id=? AND e.privacy_class='normal' AND d.event_id=?""",
                                          (project_id, thread_id, query[6:].strip())).fetchall()
            else:
                terms = re.findall(r"\w+", query)[:32]
                if not terms:
                    return []
                match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                rows = connection.execute("""SELECT d.* FROM raw_events_fts f
                    JOIN raw_event_documents d ON d.event_id=f.event_id JOIN events e ON e.id=d.event_id
                    WHERE raw_events_fts MATCH ? AND d.project_id=? AND d.thread_id=? AND e.privacy_class='normal'
                    ORDER BY bm25(raw_events_fts), d.sequence DESC LIMIT ?""",
                                          (match, project_id, thread_id, k)).fetchall()
        return [excerpt(dict(row), query) for row in rows]

    def read(self, project_id, thread_id, event_id, start=0, end=None):
        self._scope(project_id, thread_id)
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            raise ValueError("range start must be nonnegative")
        with self.store.connect() as connection:
            row = connection.execute("""SELECT d.* FROM raw_event_documents d JOIN events e ON e.id=d.event_id
                WHERE d.project_id=? AND d.thread_id=? AND d.event_id=? AND e.privacy_class='normal'""",
                                     (project_id, thread_id, event_id)).fetchone()
        if row is None:
            return None
        if end is not None and (not isinstance(end, int) or isinstance(end, bool) or end <= start):
            raise ValueError("range end must exceed start")
        if start > len(row["text"]):
            raise ValueError("range start exceeds source length")
        return excerpt(dict(row), start=start, limit=min(4096, end - start if end is not None else 4096))

    async def _embed(self, texts, *, thread_id, stage):
        if not self.url or not self.model:
            raise ValueError("embeddings endpoint/model is not configured")
        from .telemetry import endpoint_identity
        endpoint = self.url.rstrip("/")
        if not endpoint.endswith("/embeddings"):
            endpoint += "/embeddings" if endpoint.endswith("/v1") else "/v1/embeddings"
        headers = {"authorization": "Bearer " + self.api_key} if self.api_key else {}
        started = time.perf_counter()
        record = {"stage": stage, "thread_id": thread_id, "model": self.model,
                  "endpoint": endpoint_identity(endpoint), "prompt_version": "raw-embedding-v1",
                  "input_hash": sha256(canonical_json(texts).encode()).hexdigest(), "status": "FAILED"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.post(endpoint, json={"model": self.model, "input": texts}, headers=headers)
                response.raise_for_status()
                payload = response.json()
            data = sorted(payload["data"], key=lambda entry: entry["index"])
            if [entry["index"] for entry in data] != list(range(len(texts))):
                raise ValueError("embedding indices are incomplete")
            vectors = [entry["embedding"] for entry in data]
            if not vectors or len({len(vector) for vector in vectors}) != 1:
                raise ValueError("embedding dimensions differ")
            for vector in vectors:
                if not 1 <= len(vector) <= 8192 or any(not isinstance(v, (float, int)) or isinstance(v, bool)
                                                       or not math.isfinite(v) for v in vector):
                    raise ValueError("invalid embedding vector")
                if not sum(v * v for v in vector):
                    raise ValueError("embedding vector has zero norm")
            usage = payload.get("usage") or {}
            record.update(status="SUCCEEDED", input_tokens=usage.get("prompt_tokens"), output_tokens=0,
                          raw_response_hash=sha256(canonical_json(data).encode()).hexdigest())
            return vectors
        except Exception:
            record["error"] = "embedding request or validation failed"
            raise ValueError("embedding request or validation failed") from None
        finally:
            record["wall_ms"] = (time.perf_counter() - started) * 1000
            self.store.record_model_run(record)

    async def index_embeddings(self, project_id, thread_id, batch_size=32):
        self._scope(project_id, thread_id)
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 64:
            raise ValueError("batch_size must be between 1 and 64")
        with self.store.connect() as connection:
            rows = connection.execute("""SELECT d.*,
                COALESCE((SELECT MAX(v.view_end) FROM raw_event_embeddings v
                          WHERE v.event_id=d.event_id AND v.index_id=?), 0) AS indexed_end
                FROM raw_event_documents d JOIN events e ON e.id=d.event_id
                WHERE d.project_id=? AND d.thread_id=? AND e.privacy_class='normal' AND length(d.text)>0
                AND NOT EXISTS(SELECT 1 FROM raw_event_embeddings v WHERE v.event_id=d.event_id
                               AND v.index_id=? AND v.view_end=length(d.text))
                ORDER BY d.sequence LIMIT ?""", (self.index_id, project_id, thread_id, self.index_id, batch_size)).fetchall()
        chunks = []
        for row in rows:
            for start in range(row["indexed_end"], len(row["text"]), CHUNK_CHARS):
                end = min(len(row["text"]), start + CHUNK_CHARS)
                chunks.append((row["event_id"], start, end, row["text"][start:end]))
                if len(chunks) == batch_size:
                    break
            if len(chunks) == batch_size:
                break
        if not chunks:
            return {"indexed_chunks": 0}
        vectors = await self._embed([chunk[3] for chunk in chunks], thread_id=thread_id, stage="embedding_index")
        with self.store.transaction(immediate=True) as connection:
            for chunk, vector in zip(chunks, vectors):
                # Recheck privacy/scope after network I/O.
                if connection.execute("SELECT 1 FROM events WHERE id=? AND project_id=? AND thread_id=? AND privacy_class='normal'",
                                      (chunk[0], project_id, thread_id)).fetchone():
                    connection.execute("INSERT OR IGNORE INTO raw_event_embeddings VALUES (?, ?, ?, ?, ?)",
                                       (*chunk[:3], self.index_id, canonical_json(vector)))
        return {"indexed_chunks": len(chunks)}

    async def recall(self, project_id, thread_id, query, k=5):
        lexical = self.lexical(project_id, thread_id, query, k)
        if not self.url or not self.model or query.startswith("event:"):
            return {"results": lexical, "embedding_status": "not_used"}
        try:
            with self.store.connect() as connection:
                rows = connection.execute("""SELECT d.*, v.vector_json, v.view_start FROM raw_event_embeddings v
                    JOIN raw_event_documents d ON d.event_id=v.event_id JOIN events e ON e.id=d.event_id
                    WHERE d.project_id=? AND d.thread_id=? AND v.index_id=? AND e.privacy_class='normal' LIMIT 10000""",
                                          (project_id, thread_id, self.index_id)).fetchall()
            if not rows:
                return {"results": lexical, "embedding_status": "not_indexed"}
            vector = (await self._embed([query], thread_id=thread_id, stage="embedding_query"))[0]
            norm = math.sqrt(sum(x * x for x in vector))
            scored = []
            for row in rows:
                other = json.loads(row["vector_json"])
                if len(other) != len(vector):
                    continue
                score = sum(a * b for a, b in zip(vector, other)) / (norm * math.sqrt(sum(x * x for x in other)))
                if score >= .35:
                    scored.append((score, dict(row)))
            results = list(lexical)
            seen = {hit["event_id"] for hit in results}
            for score, row in sorted(scored, key=lambda x: (-x[0], -x[1]["sequence"])):
                if row["event_id"] not in seen:
                    results.append(excerpt(row, start=row["view_start"]) | {"similarity": score})
                    seen.add(row["event_id"])
            return {"results": results[:k], "embedding_status": "used"}
        except Exception:
            return {"results": lexical, "embedding_status": "failed_open"}
