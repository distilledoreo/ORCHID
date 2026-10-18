from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    active_capsule_id TEXT,
    next_sequence INTEGER NOT NULL DEFAULT 1,
    compaction_dirty INTEGER NOT NULL DEFAULT 0,
    compaction_dirty_priority INTEGER NOT NULL DEFAULT 0,
    memory_format TEXT NOT NULL DEFAULT 'v1',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    thread_id TEXT NOT NULL REFERENCES threads(id),
    sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    role TEXT,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    privacy_class TEXT NOT NULL DEFAULT 'normal',
    token_count INTEGER,
    parent_event_id TEXT REFERENCES events(id),
    request_id TEXT,
    UNIQUE(thread_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_events_thread_sequence
    ON events(thread_id, sequence);

CREATE TABLE IF NOT EXISTS capsules (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id),
    base_capsule_id TEXT REFERENCES capsules(id),
    snapshot_start_event_id TEXT,
    snapshot_end_event_id TEXT,
    covered_start_event_id TEXT,
    covered_end_event_id TEXT,
    source_event_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    capsule_hash TEXT NOT NULL,
    content TEXT NOT NULL,
    model_metadata_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('BUILDING', 'GENERATED', 'VALIDATED', 'READY',
                  'ACTIVE', 'SUPERSEDED', 'FAILED', 'STALE')
    ),
    created_at TEXT NOT NULL,
    validated_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_capsules_thread_state
    ON capsules(thread_id, state);

CREATE TABLE IF NOT EXISTS capsule_sources (
    capsule_id TEXT NOT NULL REFERENCES capsules(id),
    event_id TEXT NOT NULL REFERENCES events(id),
    position INTEGER NOT NULL,
    PRIMARY KEY(capsule_id, event_id)
);

CREATE TABLE IF NOT EXISTS compaction_jobs (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id),
    base_capsule_id TEXT REFERENCES capsules(id),
    snapshot_start_event_id TEXT,
    snapshot_end_event_id TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('QUEUED', 'RUNNING', 'READY', 'PROMOTED',
                   'FAILED', 'STALE')
    ),
    priority INTEGER NOT NULL DEFAULT 0,
    generation INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_until TEXT,
    lease_token TEXT,
    worker_id TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_claim
    ON compaction_jobs(status, priority DESC, created_at);

CREATE TABLE IF NOT EXISTS model_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES compaction_jobs(id),
    thread_id TEXT,
    generation INTEGER,
    pass_name TEXT NOT NULL,
    stage TEXT NOT NULL,
    selector_chunk_index INTEGER,
    canonicalizer_batch_index INTEGER,
    model TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    generation_settings_json TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT,
    raw_response_hash TEXT,
    diagnostic_excerpt TEXT,
    reasoning_tokens INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    wall_ms REAL,
    finish_reason TEXT,
    status TEXT NOT NULL,
    error TEXT,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Exact-retry cache for byte-identical selector, canonicalizer, and
-- consolidator results. Cached canonicalizer hits do not authorize removing
-- that stage without a full-trace semantic gate.
CREATE TABLE IF NOT EXISTS stage_result_cache (
    cache_key TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    settings_hash TEXT NOT NULL,
    system_prompt_hash TEXT NOT NULL,
    output_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stage_result_cache_lookup
    ON stage_result_cache(
        stage, model, prompt_version, input_hash, schema_hash, settings_hash,
        system_prompt_hash
    );

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Cold semantic memory is deliberately separate from capsules and events.
-- The FTS table is maintained transactionally by SQLiteStore; it is never
-- part of the authoritative event stream or the ACTIVE-capsule CAS path.
CREATE TABLE IF NOT EXISTS long_term_memories (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    thread_id TEXT NOT NULL REFERENCES threads(id),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    retired_at TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    importance REAL NOT NULL CHECK (importance >= 0 AND importance <= 1),
    activation_score REAL NOT NULL DEFAULT 0,
    retrieval_count INTEGER NOT NULL DEFAULT 0,
    injection_count INTEGER NOT NULL DEFAULT 0,
    last_retrieved_at TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (
        status IN ('ACTIVE', 'SUPERSEDED', 'DISABLED')
    ),
    embedding_version TEXT,
    content_hash TEXT NOT NULL,
    UNIQUE(thread_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_long_term_memories_scope
    ON long_term_memories(project_id, thread_id, status);

CREATE TABLE IF NOT EXISTS memory_evidence (
    memory_id TEXT NOT NULL REFERENCES long_term_memories(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES events(id),
    PRIMARY KEY(memory_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_evidence_event
    ON memory_evidence(event_id);

CREATE TABLE IF NOT EXISTS cold_retrieval_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    thread_id TEXT NOT NULL REFERENCES threads(id),
    query TEXT NOT NULL,
    candidate_ids_json TEXT NOT NULL,
    scores_json TEXT NOT NULL,
    would_inject_ids_json TEXT NOT NULL,
    mode TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    attempted INTEGER NOT NULL DEFAULT 1,
    timed_out INTEGER NOT NULL DEFAULT 0,
    fail_open INTEGER NOT NULL DEFAULT 0,
    query_hash TEXT,
    query_preview TEXT,
    exact_candidate_count INTEGER NOT NULL DEFAULT 0,
    lexical_candidate_count INTEGER NOT NULL DEFAULT 0,
    unique_candidate_count INTEGER NOT NULL DEFAULT 0,
    threshold_candidate_count INTEGER NOT NULL DEFAULT 0,
    would_inject_count INTEGER NOT NULL DEFAULT 0,
    injected_count INTEGER NOT NULL DEFAULT 0,
    retrieved_token_estimate INTEGER NOT NULL DEFAULT 0,
    injected_token_estimate INTEGER NOT NULL DEFAULT 0,
    injected_ids_json TEXT NOT NULL DEFAULT '[]',
    error_category TEXT,
    cold_retrieval_ms REAL NOT NULL DEFAULT 0,
    input_preview TEXT,
    source_previews_json TEXT NOT NULL DEFAULT '{}',
    constructed_query TEXT,
    input_terms_json TEXT NOT NULL DEFAULT '[]',
    identifier_terms_json TEXT NOT NULL DEFAULT '[]',
    ordinary_terms_json TEXT NOT NULL DEFAULT '[]',
    fts_query TEXT,
    raw_fts_candidates_json TEXT NOT NULL DEFAULT '[]',
    ranking_details_json TEXT NOT NULL DEFAULT '[]',
    query_construction_ms REAL NOT NULL DEFAULT 0,
    db_checkout_ms REAL NOT NULL DEFAULT 0,
    fts_ms REAL NOT NULL DEFAULT 0,
    ranking_ms REAL NOT NULL DEFAULT 0,
    token_budget_ms REAL NOT NULL DEFAULT 0,
    telemetry_ms REAL NOT NULL DEFAULT 0,
    total_ms REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cold_retrieval_runs_thread
    ON cold_retrieval_runs(thread_id, created_at);

-- Provider-stream observability is operational telemetry, not conversational
-- history. Keeping it separate prevents transport failures from becoming
-- semantic events while still making cleanup and continuity auditable.
CREATE TABLE IF NOT EXISTS gateway_stream_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    thread_id TEXT NOT NULL REFERENCES threads(id),
    request_id TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    provider_started_at TEXT,
    first_token_at TEXT,
    stream_end_at TEXT,
    cleanup_completed_at TEXT,
    status TEXT NOT NULL,
    failure_category TEXT,
    bytes_forwarded INTEGER NOT NULL DEFAULT 0,
    partial_output INTEGER NOT NULL DEFAULT 0,
    error_excerpt TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gateway_stream_runs_thread
    ON gateway_stream_runs(thread_id, created_at);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS long_term_memories_fts USING fts5(
    memory_id UNINDEXED,
    project_id UNINDEXED,
    thread_id UNINDEXED,
    memory_type,
    content,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _ordered_history_overlap(known: list[str], incoming: list[str]) -> int:
    """Return the length of the reusable ordered overlap in ``incoming``."""
    _, length = _ordered_history_overlap_span(known, incoming)
    return length


def _ordered_history_overlap_span(known: list[str], incoming: list[str]) -> tuple[int, int]:
    """Return ``(start_index, length)`` of reused history inside ``incoming``.

  The reusable region is the longest contiguous block in ``incoming`` that
  exactly matches either the prefix of ``known`` or a suffix of ``known``.
  This covers standard Pi history resends and prepended control messages
  without global content-hash suppression.
    """
    if not incoming or not known:
        return (0, 0)
    maximum = min(len(known), len(incoming))
    for count in range(maximum, 0, -1):
        if incoming[:count] == known[:count]:
            return (0, count)
    best_start = 0
    best_count = 0
    for count in range(maximum, 0, -1):
        known_suffix = known[-count:]
        for start in range(0, len(incoming) - count + 1):
            if incoming[start : start + count] != known_suffix:
                continue
            if count > best_count or (count == best_count and start > best_start):
                best_start = start
                best_count = count
    return (best_start, best_count)


def _first_seen_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _subsequence_reused_indices(known: list[str], incoming: list[str]) -> list[int]:
    reused: list[int] = []
    cursor = 0
    for index, digest in enumerate(incoming):
        found = cursor
        while found < len(known) and known[found] != digest:
            found += 1
        if found >= len(known):
            continue
        reused.append(index)
        cursor = found + 1
    return reused


def _subsequence_overlap_span(known: list[str], incoming: list[str]) -> tuple[int, int]:
    """Return a contiguous incoming span that is an ordered subsequence of known.

    Extra known hashes (stream reconstructions the client never resends) are
    skipped. Incoming hashes that are not still ahead in known stay novel.
    """
    reused = _subsequence_reused_indices(known, incoming)
    if not reused:
        return (0, 0)
    best_start = reused[0]
    best_length = 1
    run_start = reused[0]
    run_length = 1
    for previous, current in zip(reused, reused[1:]):
        if current == previous + 1:
            run_length += 1
            if run_length > best_length:
                best_start = run_start
                best_length = run_length
        else:
            run_start = current
            run_length = 1
    return (best_start, best_length)


def _reused_incoming_indices(known: list[str], incoming: list[str]) -> set[int]:
    """Skip every incoming index that is an ordered resent of known history.

    A single unmatched middle message must not cause the rest of a Pi
    history resend to be appended again; compaction windows are token-bounded
    and cannot keep up with duplicated events.
    """
    start, length = _best_ordered_history_overlap_span(known, incoming)
    skipped = set(range(start, start + length))
    skipped.update(
        _subsequence_reused_indices(_first_seen_preserve_order(known), incoming)
    )
    return skipped


def _best_ordered_history_overlap_span(
    known: list[str], incoming: list[str]
) -> tuple[int, int]:
    """Reuse a resent client history even after duplicate events snowball.

    Compaction windows are token-bounded. If every Pi resend is appended
    again, those windows compact copies instead of new unique history.
    """
    full = _ordered_history_overlap_span(known, incoming)
    first_seen = _subsequence_overlap_span(
        _first_seen_preserve_order(known), incoming
    )
    if first_seen[1] >= full[1]:
        return first_seen
    return full


def _metadata_is_chat_response(raw: Any) -> bool:
    try:
        metadata = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    return (
        isinstance(metadata, dict)
        and metadata.get("ingest_kind") == "chat_message"
        and metadata.get("source") == "response"
    )


class SQLiteStore:
    """Small SQLite repository with explicit transaction boundaries."""

    def __init__(self, path: str | Path):
        self.coding_policy = None
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(
        self,
        *,
        timeout: float = 30.0,
        busy_timeout_ms: int = 30_000,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=timeout,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute(f"PRAGMA busy_timeout = {max(1, int(busy_timeout_ms))}")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            try:
                connection.executescript(FTS_SCHEMA)
            except sqlite3.OperationalError as error:
                if "fts5" not in str(error).lower():
                    raise
            self._backfill_long_term_memory_index(connection)
            self._migrate_threads(connection)
            self._migrate_compaction_jobs(connection)
            self._migrate_model_runs(connection)
            self._migrate_cold_retrieval_runs(connection)
            self._migrate_stage_result_cache(connection)
            from .coding import initialize_views
            initialize_views(connection)
            try:
                from .raw_recall import initialize_index
                initialize_index(connection)
            except Exception:
                # Derived indexing must not prevent raw storage from opening.
                pass
            connection.execute("""CREATE TABLE IF NOT EXISTS request_usage(
                thread_id TEXT NOT NULL, request_id TEXT NOT NULL, usage_json TEXT NOT NULL,
                PRIMARY KEY(thread_id, request_id))""")

    @staticmethod
    def _backfill_long_term_memory_index(connection: sqlite3.Connection) -> None:
        """Populate a newly-created FTS sidecar for an existing database."""

        try:
            rows = connection.execute(
                """
                SELECT m.id, m.project_id, m.thread_id, m.memory_type, m.content
                FROM long_term_memories AS m
                LEFT JOIN long_term_memories_fts AS f ON f.memory_id = m.id
                WHERE f.memory_id IS NULL
                """
            ).fetchall()
            connection.executemany(
                """
                INSERT INTO long_term_memories_fts(
                    memory_id, project_id, thread_id, memory_type, content
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [tuple(row) for row in rows],
            )
        except sqlite3.OperationalError as error:
            if "long_term_memories_fts" not in str(error):
                raise

    @staticmethod
    def _migrate_threads(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(threads)").fetchall()
        }
        if "compaction_dirty" not in columns:
            connection.execute(
                "ALTER TABLE threads ADD COLUMN compaction_dirty INTEGER NOT NULL DEFAULT 0"
            )
        if "compaction_dirty_priority" not in columns:
            connection.execute(
                "ALTER TABLE threads ADD COLUMN compaction_dirty_priority INTEGER NOT NULL DEFAULT 0"
            )
        if "root_task_event_id" not in columns:
            connection.execute("ALTER TABLE threads ADD COLUMN root_task_event_id TEXT")
        if "memory_format" not in columns:
            connection.execute(
                "ALTER TABLE threads ADD COLUMN memory_format TEXT NOT NULL DEFAULT 'v1'"
            )
        if "ingestion_rate_ewma" not in columns:
            connection.execute(
                "ALTER TABLE threads ADD COLUMN ingestion_rate_ewma REAL NOT NULL DEFAULT 0"
            )
        if "last_uncompacted_tokens" not in columns:
            connection.execute(
                "ALTER TABLE threads ADD COLUMN last_uncompacted_tokens INTEGER NOT NULL DEFAULT 0"
            )
        if "last_prompt_cache_json" not in columns:
            connection.execute("ALTER TABLE threads ADD COLUMN last_prompt_cache_json TEXT")

    @staticmethod
    def _migrate_stage_result_cache(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS stage_result_cache (
                cache_key TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                schema_hash TEXT NOT NULL,
                settings_hash TEXT NOT NULL,
                system_prompt_hash TEXT NOT NULL,
                output_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stage_result_cache_lookup
                ON stage_result_cache(
                    stage, model, prompt_version, input_hash, schema_hash,
                    settings_hash, system_prompt_hash
                )
            """
        )

    @staticmethod
    def _migrate_compaction_jobs(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(compaction_jobs)"
            ).fetchall()
        }
        if "lease_token" not in columns:
            connection.execute(
                "ALTER TABLE compaction_jobs ADD COLUMN lease_token TEXT"
            )

    @staticmethod
    def _migrate_model_runs(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(model_runs)").fetchall()
        }
        required = {
            "thread_id",
            "generation",
            "stage",
            "selector_chunk_index",
            "canonicalizer_batch_index",
            "endpoint",
            "prompt_version",
            "generation_settings_json",
            "source_refs_json",
            "raw_response_hash",
            "diagnostic_excerpt",
            "finish_reason",
            "error",
        }
        job_id_column = columns.get("job_id")
        job_id_is_nullable = job_id_column is not None and not bool(
            job_id_column["notnull"]
        )
        if required.issubset(columns) and job_id_is_nullable:
            return

        connection.execute("ALTER TABLE model_runs RENAME TO model_runs_legacy_v1")
        connection.execute(
            """
            CREATE TABLE model_runs (
                id TEXT PRIMARY KEY,
                job_id TEXT REFERENCES compaction_jobs(id),
                thread_id TEXT,
                generation INTEGER,
                pass_name TEXT NOT NULL,
                stage TEXT NOT NULL,
                selector_chunk_index INTEGER,
                canonicalizer_batch_index INTEGER,
                model TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                generation_settings_json TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                output_hash TEXT,
                raw_response_hash TEXT,
                diagnostic_excerpt TEXT,
                reasoning_tokens INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                wall_ms REAL,
                finish_reason TEXT,
                status TEXT NOT NULL,
                error TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO model_runs(
                id, job_id, thread_id, generation, pass_name, stage,
                selector_chunk_index, canonicalizer_batch_index, model,
                endpoint, prompt_version, generation_settings_json,
                source_refs_json, input_hash, output_hash, raw_response_hash,
                diagnostic_excerpt, reasoning_tokens, input_tokens,
                output_tokens, wall_ms, finish_reason, status, metadata_json,
                error, created_at
            )
            SELECT
                legacy.id,
                legacy.job_id,
                jobs.thread_id,
                jobs.generation,
                legacy.pass_name,
                legacy.pass_name,
                NULL,
                NULL,
                legacy.model,
                '',
                '',
                '{}',
                '[]',
                legacy.input_hash,
                legacy.output_hash,
                legacy.output_hash,
                NULL,
                legacy.reasoning_tokens,
                legacy.input_tokens,
                legacy.output_tokens,
                legacy.wall_ms,
                NULL,
                legacy.status,
                legacy.metadata_json,
                NULL,
                legacy.created_at
            FROM model_runs_legacy_v1 AS legacy
            LEFT JOIN compaction_jobs AS jobs ON jobs.id = legacy.job_id
            """
        )
        connection.execute("DROP TABLE model_runs_legacy_v1")

    @staticmethod
    def _migrate_cold_retrieval_runs(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(cold_retrieval_runs)"
            ).fetchall()
        }
        additions = {
            "attempted": "INTEGER NOT NULL DEFAULT 1",
            "timed_out": "INTEGER NOT NULL DEFAULT 0",
            "fail_open": "INTEGER NOT NULL DEFAULT 0",
            "query_hash": "TEXT",
            "query_preview": "TEXT",
            "exact_candidate_count": "INTEGER NOT NULL DEFAULT 0",
            "lexical_candidate_count": "INTEGER NOT NULL DEFAULT 0",
            "unique_candidate_count": "INTEGER NOT NULL DEFAULT 0",
            "threshold_candidate_count": "INTEGER NOT NULL DEFAULT 0",
            "would_inject_count": "INTEGER NOT NULL DEFAULT 0",
            "injected_count": "INTEGER NOT NULL DEFAULT 0",
            "retrieved_token_estimate": "INTEGER NOT NULL DEFAULT 0",
            "injected_token_estimate": "INTEGER NOT NULL DEFAULT 0",
            "injected_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "error_category": "TEXT",
            "cold_retrieval_ms": "REAL NOT NULL DEFAULT 0",
            "input_preview": "TEXT",
            "source_previews_json": "TEXT NOT NULL DEFAULT '{}'",
            "constructed_query": "TEXT",
            "input_terms_json": "TEXT NOT NULL DEFAULT '[]'",
            "identifier_terms_json": "TEXT NOT NULL DEFAULT '[]'",
            "ordinary_terms_json": "TEXT NOT NULL DEFAULT '[]'",
            "fts_query": "TEXT",
            "raw_fts_candidates_json": "TEXT NOT NULL DEFAULT '[]'",
            "ranking_details_json": "TEXT NOT NULL DEFAULT '[]'",
            "query_construction_ms": "REAL NOT NULL DEFAULT 0",
            "db_checkout_ms": "REAL NOT NULL DEFAULT 0",
            "fts_ms": "REAL NOT NULL DEFAULT 0",
            "ranking_ms": "REAL NOT NULL DEFAULT 0",
            "token_budget_ms": "REAL NOT NULL DEFAULT 0",
            "telemetry_ms": "REAL NOT NULL DEFAULT 0",
            "total_ms": "REAL NOT NULL DEFAULT 0",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE cold_retrieval_runs ADD COLUMN {name} {definition}"
                )

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_thread(self, thread_id: str) -> int:
        """Remove a thread and everything derived from it; returns the events removed.

        secure_delete overwrites the freed pages and the WAL is truncated afterwards,
        so the thread's text doesn't stay readable in the database files.
        """
        connection = self.connect()
        try:
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("BEGIN IMMEDIATE")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            events = [row[0] for row in connection.execute("SELECT id FROM events WHERE thread_id=?", (thread_id,))]
            capsules = [row[0] for row in connection.execute("SELECT id FROM capsules WHERE thread_id=?", (thread_id,))]
            memories = [row[0] for row in connection.execute(
                "SELECT id FROM long_term_memories WHERE thread_id=?", (thread_id,))]

            def delete(table, column, values):
                if table in tables and values:
                    for start in range(0, len(values), 500):
                        part = values[start:start + 500]
                        connection.execute(f"DELETE FROM {table} WHERE {column} IN ({','.join('?' * len(part))})", part)
            for table in ("raw_event_documents", "raw_events_fts", "raw_event_embeddings", "capsule_sources",
                          "memory_evidence"):
                delete(table, "event_id", events)
            delete("capsule_sources", "capsule_id", capsules)
            delete("coding_prompt_views", "capsule_id", capsules)
            delete("memory_evidence", "memory_id", memories)
            delete("long_term_memories_fts", "memory_id", memories)
            for table in ("long_term_memories", "capsules", "compaction_jobs", "model_runs", "cold_retrieval_runs",
                          "gateway_stream_runs", "request_usage", "events"):
                if table in tables:
                    connection.execute(f"DELETE FROM {table} WHERE thread_id=?", (thread_id,))
            connection.execute("DELETE FROM threads WHERE id=?", (thread_id,))
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return len(events)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_project(self, project_id: str, name: str | None = None) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO projects(id, name, created_at) VALUES (?, ?, ?)",
                (project_id, name or project_id, utc_now()),
            )

    def create_thread(
        self,
        thread_id: str,
        project_id: str,
        *,
        memory_format: str = "v1",
    ) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO threads(id, project_id, memory_format, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, project_id, memory_format, utc_now()),
            )

    def ensure_project_and_thread(
        self,
        project_id: str,
        thread_id: str,
        *,
        memory_format: str = "v1",
    ) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO projects(id, name, created_at) VALUES (?, ?, ?)",
                (project_id, project_id, utc_now()),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO threads(id, project_id, memory_format, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, project_id, memory_format, utc_now()),
            )

    def get_thread_memory_format(self, thread_id: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT memory_format FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
        if row is None:
            return "v1"
        return str(row["memory_format"] or "v1")

    def get_thread_compaction_forecast(self, thread_id: str) -> tuple[float, int]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT ingestion_rate_ewma, last_uncompacted_tokens
                FROM threads
                WHERE id = ?
                """,
                (thread_id,),
            ).fetchone()
        if row is None:
            return 0.0, 0
        return float(row["ingestion_rate_ewma"] or 0.0), int(row["last_uncompacted_tokens"] or 0)

    def update_thread_compaction_forecast(
        self,
        thread_id: str,
        uncompacted_tokens: int,
        *,
        alpha: float = 0.2,
    ) -> tuple[float, int]:
        from .compaction_forecast import CompactionForecast

        rate_ewma, last_tokens = self.get_thread_compaction_forecast(thread_id)
        forecast = CompactionForecast(
            rate_ewma=rate_ewma,
            last_uncompacted_tokens=last_tokens,
        ).observe(uncompacted_tokens, alpha=alpha)
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE threads
                SET ingestion_rate_ewma = ?, last_uncompacted_tokens = ?
                WHERE id = ?
                """,
                (forecast.rate_ewma, forecast.last_uncompacted_tokens, thread_id),
            )
        return forecast.rate_ewma, forecast.last_uncompacted_tokens

    def record_thread_prompt_cache(self, thread_id: str, payload: dict[str, Any]) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE threads
                SET last_prompt_cache_json = ?
                WHERE id = ?
                """,
                (canonical_json(payload), thread_id),
            )

    def get_thread_prompt_cache(self, thread_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT last_prompt_cache_json FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
        raw = None if row is None else row["last_prompt_cache_json"]
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def get_latest_ready_capsule(self, thread_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM capsules
                WHERE thread_id = ? AND state = 'READY'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
        return dict(row) if row else None

    def stale_ready_capsules(self, thread_id: str, *, except_capsule_id: str | None = None) -> int:
        with self.transaction(immediate=True) as connection:
            if except_capsule_id:
                result = connection.execute(
                    """
                    UPDATE capsules
                    SET state = 'STALE'
                    WHERE thread_id = ? AND state = 'READY' AND id != ?
                    """,
                    (thread_id, except_capsule_id),
                )
            else:
                result = connection.execute(
                    """
                    UPDATE capsules
                    SET state = 'STALE'
                    WHERE thread_id = ? AND state = 'READY'
                    """,
                    (thread_id,),
                )
            return int(result.rowcount)

    def record_request_usage(self, thread_id: str, request_id: str, usage: dict) -> None:
        # Persist only numeric usage counters, never arbitrary provider payloads.
        allowed = {key: value for key, value in usage.items()
                   if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                   and isinstance(value, int) and not isinstance(value, bool) and value >= 0}
        with self.transaction(immediate=True) as connection:
            connection.execute("INSERT OR IGNORE INTO request_usage VALUES (?, ?, ?)",
                               (thread_id, request_id, canonical_json(allowed)))

    def append_event(
        self,
        *,
        project_id: str,
        thread_id: str,
        event_type: str,
        content: str,
        role: str | None = None,
        metadata: dict[str, Any] | None = None,
        privacy_class: str = "normal",
        token_count: int | None = None,
        parent_event_id: str | None = None,
        request_id: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        event_id = event_id or new_id("evt")
        metadata_json = canonical_json(metadata or {})
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT next_sequence FROM threads WHERE id = ? AND project_id = ?",
                (thread_id, project_id),
            ).fetchone()
            if row is None:
                raise ValueError("thread does not belong to project")
            sequence = int(row["next_sequence"])
            created_at = utc_now()
            event = {
                "id": event_id,
                "project_id": project_id,
                "thread_id": thread_id,
                "sequence": sequence,
                "created_at": created_at,
                "event_type": event_type,
                "role": role,
                "content": content,
                "metadata_json": metadata_json,
                "content_hash": content_hash(content),
                "privacy_class": privacy_class,
                "token_count": token_count,
                "parent_event_id": parent_event_id,
                "request_id": request_id,
            }
            connection.execute(
                """
                INSERT INTO events(
                    id, project_id, thread_id, sequence, created_at, event_type,
                    role, content, metadata_json, content_hash, privacy_class,
                    token_count, parent_event_id, request_id
                ) VALUES (
                    :id, :project_id, :thread_id, :sequence, :created_at, :event_type,
                    :role, :content, :metadata_json, :content_hash, :privacy_class,
                    :token_count, :parent_event_id, :request_id
                )
                """,
                event,
            )
            from .raw_recall import index_event_fail_open
            index_event_fail_open(connection, event)
            connection.execute(
                "UPDATE threads SET next_sequence = ? WHERE id = ?",
                (sequence + 1, thread_id),
            )
            return event

    def append_novel_chat_messages(
        self,
        *,
        project_id: str,
        thread_id: str,
        messages: tuple[dict[str, Any], ...],
        request_id: str | None,
        source: str,
        usage: dict | None = None,
    ) -> Any:
        """Append new chat occurrences while suppressing protocol replays.

        OpenAI-compatible clients resend the prior message history on every
        request. Request history uses exact canonical message hashes plus
        sequence/order overlap. Response-only ingestion is keyed by request ID
        instead, so identical assistant responses from distinct requests remain
        distinct occurrences.
        """
        from .context import estimate_tokens
        from .ingestion import message_content, message_event_type, message_hash

        incoming_hashes = [message_hash(message) for message in messages]
        with self.transaction(immediate=True) as connection:
            thread = connection.execute(
                "SELECT next_sequence FROM threads WHERE id = ? AND project_id = ?",
                (thread_id, project_id),
            ).fetchone()
            if thread is None:
                raise ValueError("thread does not belong to project")
            rows = connection.execute(
                """
                SELECT id, content_hash, metadata_json, role, request_id
                FROM events
                WHERE thread_id = ?
                ORDER BY sequence
                """,
                (thread_id,),
            ).fetchall()
            known_hashes: list[str] = []
            known_ids: list[str] = []
            for row in rows:
                try:
                    metadata = json.loads(row["metadata_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if metadata.get("ingest_kind") != "chat_message":
                    continue
                digest = metadata.get("message_hash")
                if isinstance(digest, str):
                    known_hashes.append(digest)
                    known_ids.append(str(row["id"]))

            response_replay = (
                source == "response"
                and request_id is not None
                and any(
                    row["request_id"] == request_id
                    and _metadata_is_chat_response(row["metadata_json"])
                    for row in rows
                )
            )
            if source == "response":
                reused_start = 0
                reused = len(messages) if response_replay else 0
                reused_indices = set(range(len(messages))) if response_replay else set()
            else:
                reused_start, reused = _best_ordered_history_overlap_span(
                    known_hashes, incoming_hashes
                )
                reused_indices = _reused_incoming_indices(known_hashes, incoming_hashes)
            event_ids: list[str | None] = [None] * len(messages)
            # Match from the right so a truncated resend maps to the latest
            # occurrence. Persisted request assembly uses these exact identities.
            cursor = len(known_hashes) - 1
            for index in sorted(reused_indices, reverse=True):
                while cursor >= 0 and known_hashes[cursor] != incoming_hashes[index]:
                    cursor -= 1
                if cursor >= 0:
                    event_ids[index] = known_ids[cursor]
                    cursor -= 1
            if response_replay:
                response_rows = [row for row in rows if row["request_id"] == request_id
                                 and _metadata_is_chat_response(row["metadata_json"])]
                event_ids = [str(row["id"]) for row in response_rows][:len(messages)]
            completion_tokens = (usage or {}).get("completion_tokens")
            real_completion = (source == "response" and len(messages) == 1
                               and isinstance(completion_tokens, int)
                               and not isinstance(completion_tokens, bool) and completion_tokens >= 0)
            appended: list[dict[str, Any]] = []
            skipped_duplicate_count = 0
            sequence = int(thread["next_sequence"])
            parent_event_id = known_ids[-1] if known_ids else None
            for index, message in enumerate(messages):
                if index in reused_indices:
                    skipped_duplicate_count += 1
                    continue
                digest = incoming_hashes[index]
                content = message_content(message)
                event_id = new_id("evt")
                event = {
                    "id": event_id,
                    "project_id": project_id,
                    "thread_id": thread_id,
                    "sequence": sequence,
                    "created_at": utc_now(),
                    "event_type": message_event_type(message.get("role")),
                    "role": message.get("role"),
                    "content": content,
                    "metadata_json": canonical_json(
                        {
                            "ingest_kind": "chat_message",
                            "source": source,
                            "message_index": index,
                            "message_hash": digest,
                            "request_id": request_id,
                            "token_count_kind": "provider_completion" if real_completion else "estimate",
                        }
                    ),
                    "content_hash": content_hash(content),
                    "privacy_class": "normal",
                    "token_count": completion_tokens if real_completion else estimate_tokens(content),
                    "parent_event_id": parent_event_id,
                    "request_id": request_id,
                }
                connection.execute(
                    """
                    INSERT INTO events(
                        id, project_id, thread_id, sequence, created_at, event_type,
                        role, content, metadata_json, content_hash, privacy_class,
                        token_count, parent_event_id, request_id
                    ) VALUES (
                        :id, :project_id, :thread_id, :sequence, :created_at,
                        :event_type, :role, :content, :metadata_json, :content_hash,
                        :privacy_class, :token_count, :parent_event_id, :request_id
                    )
                    """,
                    event,
                )
                if message.get("role") == "user":
                    connection.execute(
                        """
                        UPDATE threads
                        SET root_task_event_id = ?
                        WHERE id = ? AND root_task_event_id IS NULL
                        """,
                        (event_id, thread_id),
                    )
                from .raw_recall import index_event_fail_open
                index_event_fail_open(connection, event)
                event_ids[index] = event_id
                appended.append(event)
                parent_event_id = event_id
                sequence += 1
            connection.execute(
                "UPDATE threads SET next_sequence = ? WHERE id = ?",
                (sequence, thread_id),
            )
        return {
            "appended_events": tuple(appended),
            "message_event_ids": tuple(event_ids),
            "reused_prefix_count": reused,
            "reused_prefix_start": reused_start,
            "known_message_count": len(known_hashes),
            "incoming_message_count": len(messages),
            "skipped_duplicate_count": skipped_duplicate_count,
            "duplicate_protection": (
                "response_request_id"
                if source == "response"
                else "ordered_canonical_message_hash_overlap"
            ),
        }

    def create_gateway_stream_run(
        self,
        *,
        project_id: str,
        thread_id: str,
        request_id: str,
    ) -> str:
        run_id = new_id("stream")
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO gateway_stream_runs(
                    id, project_id, thread_id, request_id, accepted_at,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'ACCEPTED', ?)
                """,
                (run_id, project_id, thread_id, request_id, now, now),
            )
        return run_id

    def update_gateway_stream_run(self, run_id: str, **updates: Any) -> None:
        allowed = {
            "provider_started_at",
            "first_token_at",
            "stream_end_at",
            "cleanup_completed_at",
            "status",
            "failure_category",
            "bytes_forwarded",
            "partial_output",
            "error_excerpt",
        }
        values = {key: value for key, value in updates.items() if key in allowed}
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.transaction(immediate=True) as connection:
            connection.execute(
                f"UPDATE gateway_stream_runs SET {assignments} WHERE id = ?",
                (*values.values(), run_id),
            )

    def list_gateway_stream_runs(
        self,
        *,
        thread_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if thread_id is not None:
            clauses.append("thread_id = ?")
            params.append(thread_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 5000)))
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM gateway_stream_runs
                {where}
                ORDER BY created_at, id
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_events(
        self,
        thread_id: str,
        *,
        start_sequence: int | None = None,
        end_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["thread_id = ?"]
        params: list[Any] = [thread_id]
        if start_sequence is not None:
            clauses.append("sequence >= ?")
            params.append(start_sequence)
        if end_sequence is not None:
            clauses.append("sequence <= ?")
            params.append(end_sequence)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY sequence",
                params,
            ).fetchall()
        return [dict(row) for row in rows]
    def latest_event(self, thread_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE thread_id = ? ORDER BY sequence DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    def get_root_task_event(self, thread_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT root_task_event_id FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
        if row is None or not row["root_task_event_id"]:
            return None
        return self.get_event(str(row["root_task_event_id"]))

    def get_active_capsule(self, thread_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT c.*
                FROM threads t
                JOIN capsules c ON c.id = t.active_capsule_id
                WHERE t.id = ?
                """,
                (thread_id,),
            ).fetchone()
        return dict(row) if row else None

    def persist_long_term_memories(
        self,
        *,
        thread_id: str,
        memories: list[dict[str, Any]],
    ) -> tuple[str, ...]:
        """Persist RETIRE decisions without touching capsules or raw events.

        The caller supplies already-normalized event IDs. A content hash makes
        retries idempotent while provenance rows remain additive.
        """

        if not memories:
            return ()
        with self.transaction(immediate=True) as connection:
            thread = connection.execute(
                "SELECT project_id FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
            if thread is None:
                raise ValueError("thread does not exist")
            project_id = str(thread["project_id"])
            persisted_ids: list[str] = []
            for memory in memories:
                content = str(memory.get("content") or "").strip()
                memory_type = str(memory.get("memory_type") or "").strip()
                importance = float(memory.get("importance", 0.5))
                evidence_event_ids = tuple(
                    str(event_id) for event_id in memory.get("evidence_event_ids", ())
                )
                if not content or not memory_type:
                    raise ValueError("RETIRE memory content and memory_type are required")
                if not 0 <= importance <= 1:
                    raise ValueError("RETIRE memory importance must be between 0 and 1")
                if not evidence_event_ids:
                    raise ValueError("RETIRE memory requires provenance evidence")
                event_placeholders = ", ".join("?" for _ in evidence_event_ids)
                event_rows = connection.execute(
                    f"""
                    SELECT id FROM events
                    WHERE thread_id = ? AND id IN ({event_placeholders})
                    """,
                    (thread_id, *evidence_event_ids),
                ).fetchall()
                if {str(row["id"]) for row in event_rows} != set(evidence_event_ids):
                    raise ValueError("RETIRE memory provenance contains an unknown event")

                digest = content_hash(content)
                existing = connection.execute(
                    """
                    SELECT id FROM long_term_memories
                    WHERE thread_id = ? AND content_hash = ?
                    """,
                    (thread_id, digest),
                ).fetchone()
                if existing is None:
                    memory_id = str(memory.get("id") or new_id("mem"))
                    now = utc_now()
                    connection.execute(
                        """
                        INSERT INTO long_term_memories(
                            id, project_id, thread_id, content, created_at,
                            retired_at, memory_type, importance, activation_score,
                            content_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            memory_id,
                            project_id,
                            thread_id,
                            content,
                            now,
                            now,
                            memory_type,
                            importance,
                            importance,
                            digest,
                        ),
                    )
                    try:
                        connection.execute(
                            """
                            INSERT INTO long_term_memories_fts(
                                memory_id, project_id, thread_id, memory_type, content
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (memory_id, project_id, thread_id, memory_type, content),
                        )
                    except sqlite3.OperationalError as error:
                        if "long_term_memories_fts" not in str(error):
                            raise
                else:
                    memory_id = str(existing["id"])
                    connection.execute(
                        """
                        UPDATE long_term_memories
                        SET importance = MAX(importance, ?),
                            activation_score = MAX(activation_score, ?),
                            retired_at = ?
                        WHERE id = ?
                        """,
                        (importance, importance, utc_now(), memory_id),
                    )
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO memory_evidence(memory_id, event_id)
                    VALUES (?, ?)
                    """,
                    [(memory_id, event_id) for event_id in evidence_event_ids],
                )
                persisted_ids.append(memory_id)
            return tuple(persisted_ids)

    def search_long_term_memories(
        self,
        *,
        project_id: str,
        thread_id: str,
        fts_query: str,
        limit: int = 20,
        timeout_seconds: float | None = None,
        timings: dict[str, float] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a bounded lexical search over ACTIVE cold memories."""

        if not fts_query.strip() or limit <= 0:
            return []
        deadline = (
            None
            if timeout_seconds is None
            else time.monotonic() + max(0, timeout_seconds)
        )
        connection_timeout = (
            30.0 if timeout_seconds is None else max(0.001, timeout_seconds)
        )
        checkout_started = time.perf_counter()
        with self.connect(
            timeout=connection_timeout,
            busy_timeout_ms=int(connection_timeout * 1000),
        ) as connection:
            if timings is not None:
                timings["db_checkout_ms"] = (time.perf_counter() - checkout_started) * 1000
            if deadline is not None:
                connection.set_progress_handler(
                    lambda: 1
                    if time.monotonic() >= deadline
                    else 0,
                    500,
                )
            fts_started = time.perf_counter()
            try:
                rows = connection.execute(
                    """
                    SELECT m.*, bm25(long_term_memories_fts) AS bm25_score
                    FROM long_term_memories_fts
                    JOIN long_term_memories AS m
                      ON m.id = long_term_memories_fts.memory_id
                    WHERE long_term_memories_fts MATCH ?
                      AND m.project_id = ?
                      AND m.thread_id = ?
                      AND m.status = 'ACTIVE'
                    ORDER BY bm25_score ASC, m.created_at DESC
                    LIMIT ?
                    """,
                    (fts_query, project_id, thread_id, limit),
                ).fetchall()
            finally:
                if timings is not None:
                    timings["fts_ms"] = (time.perf_counter() - fts_started) * 1000
                if deadline is not None:
                    connection.set_progress_handler(None, 0)
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("cold-memory search timed out")
        return [dict(row) for row in rows]

    def record_memory_retrieval(
        self,
        *,
        memory_ids: tuple[str, ...] | list[str],
        injected_ids: tuple[str, ...] | list[str] = (),
    ) -> None:
        """Record sidecar reinforcement only; this never appends an event."""

        retrieval_ids = tuple(dict.fromkeys(memory_ids))
        injection_ids = tuple(dict.fromkeys(injected_ids))
        if not retrieval_ids and not injection_ids:
            return
        with self.transaction(immediate=True) as connection:
            self._apply_memory_retrieval(
                connection,
                memory_ids=retrieval_ids,
                injected_ids=injection_ids,
            )

    @staticmethod
    def _apply_memory_retrieval(
        connection: sqlite3.Connection,
        *,
        memory_ids: tuple[str, ...] | list[str],
        injected_ids: tuple[str, ...] | list[str],
    ) -> None:
        now = utc_now()
        for memory_id in memory_ids:
            connection.execute(
                """
                UPDATE long_term_memories
                SET retrieval_count = retrieval_count + 1,
                    last_retrieved_at = ?
                WHERE id = ? AND status = 'ACTIVE'
                """,
                (now, memory_id),
            )
        for memory_id in injected_ids:
            connection.execute(
                """
                UPDATE long_term_memories
                SET injection_count = injection_count + 1,
                    activation_score = MIN(1, activation_score + 0.01)
                WHERE id = ? AND status = 'ACTIVE'
                """,
                (memory_id,),
            )

    def record_cold_retrieval_run(
        self,
        *,
        project_id: str,
        thread_id: str,
        query: str,
        candidate_ids: tuple[str, ...] | list[str],
        scores: tuple[float, ...] | list[float],
        would_inject_ids: tuple[str, ...] | list[str],
        mode: str,
        latency_ms: float,
        status: str,
        error: str | None = None,
        attempted: bool = True,
        timed_out: bool = False,
        fail_open: bool = False,
        query_hash: str | None = None,
        query_preview: str | None = None,
        exact_candidate_count: int = 0,
        lexical_candidate_count: int = 0,
        unique_candidate_count: int = 0,
        threshold_candidate_count: int = 0,
        would_inject_count: int = 0,
        injected_count: int = 0,
        retrieved_token_estimate: int = 0,
        injected_token_estimate: int = 0,
        injected_ids: tuple[str, ...] | list[str] = (),
        error_category: str | None = None,
        cold_retrieval_ms: float | None = None,
        input_preview: str | None = None,
        source_previews: dict[str, str] | None = None,
        constructed_query: str | None = None,
        input_terms: tuple[str, ...] | list[str] = (),
        identifier_terms: tuple[str, ...] | list[str] = (),
        ordinary_terms: tuple[str, ...] | list[str] = (),
        fts_query: str | None = None,
        raw_fts_candidates: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
        ranking_details: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
        query_construction_ms: float = 0.0,
        db_checkout_ms: float = 0.0,
        fts_ms: float = 0.0,
        ranking_ms: float = 0.0,
        token_budget_ms: float = 0.0,
        telemetry_ms: float = 0.0,
        total_ms: float | None = None,
        run_id: str | None = None,
    ) -> str:
        payload = self.prepare_cold_retrieval_run(
            run_id=run_id,
            project_id=project_id,
            thread_id=thread_id,
            query=query,
            candidate_ids=candidate_ids,
            scores=scores,
            would_inject_ids=would_inject_ids,
            mode=mode,
            latency_ms=latency_ms,
            status=status,
            error=error,
            attempted=attempted,
            timed_out=timed_out,
            fail_open=fail_open,
            query_hash=query_hash,
            query_preview=query_preview,
            exact_candidate_count=exact_candidate_count,
            lexical_candidate_count=lexical_candidate_count,
            unique_candidate_count=unique_candidate_count,
            threshold_candidate_count=threshold_candidate_count,
            would_inject_count=would_inject_count,
            injected_count=injected_count,
            retrieved_token_estimate=retrieved_token_estimate,
            injected_token_estimate=injected_token_estimate,
            injected_ids=injected_ids,
            error_category=error_category,
            cold_retrieval_ms=cold_retrieval_ms,
            input_preview=input_preview,
            source_previews=source_previews,
            constructed_query=constructed_query,
            input_terms=input_terms,
            identifier_terms=identifier_terms,
            ordinary_terms=ordinary_terms,
            fts_query=fts_query,
            raw_fts_candidates=raw_fts_candidates,
            ranking_details=ranking_details,
            query_construction_ms=query_construction_ms,
            db_checkout_ms=db_checkout_ms,
            fts_ms=fts_ms,
            ranking_ms=ranking_ms,
            token_budget_ms=token_budget_ms,
            telemetry_ms=telemetry_ms,
            total_ms=total_ms,
        )
        run_id = str(payload["id"])
        record_started = time.perf_counter()
        with self.transaction(immediate=True) as connection:
            self._insert_cold_retrieval_run(connection, payload)
            measured_telemetry_ms = (time.perf_counter() - record_started) * 1000
            connection.execute(
                """
                UPDATE cold_retrieval_runs
                SET telemetry_ms = ?, total_ms = ?
                WHERE id = ?
                """,
                (
                    measured_telemetry_ms,
                    float(total_ms if total_ms is not None else latency_ms)
                    + measured_telemetry_ms,
                    run_id,
                ),
            )
        return run_id

    def prepare_cold_retrieval_run(self, **kwargs: Any) -> dict[str, Any]:
        """Build a bounded cold-run row without performing a database write."""

        query = str(kwargs.get("query") or "")
        query_preview = (kwargs.get("query_preview") or query)[:240]
        error = kwargs.get("error")
        error_category = kwargs.get("error_category")
        cold_retrieval_ms = kwargs.get("cold_retrieval_ms")
        payload = {
            "id": str(kwargs.get("run_id") or new_id("coldrun")),
            "project_id": str(kwargs.get("project_id") or ""),
            "thread_id": str(kwargs.get("thread_id") or ""),
            "query": query_preview,
            "candidate_ids_json": canonical_json(list(kwargs.get("candidate_ids") or ())),
            "scores_json": canonical_json(
                [round(float(score), 6) for score in kwargs.get("scores") or ()]
            ),
            "would_inject_ids_json": canonical_json(
                list(kwargs.get("would_inject_ids") or ())
            ),
            "mode": str(kwargs.get("mode") or "shadow"),
            "latency_ms": float(kwargs.get("latency_ms") or 0.0),
            "status": str(kwargs.get("status") or "ok"),
            "error": str(error)[:240] if error else None,
            "created_at": str(kwargs.get("created_at") or utc_now()),
            "attempted": int(bool(kwargs.get("attempted", True))),
            "timed_out": int(bool(kwargs.get("timed_out", False))),
            "fail_open": int(bool(kwargs.get("fail_open", False))),
            "query_hash": kwargs.get("query_hash"),
            "query_preview": query_preview,
            "exact_candidate_count": int(kwargs.get("exact_candidate_count") or 0),
            "lexical_candidate_count": int(kwargs.get("lexical_candidate_count") or 0),
            "unique_candidate_count": int(kwargs.get("unique_candidate_count") or 0),
            "threshold_candidate_count": int(kwargs.get("threshold_candidate_count") or 0),
            "would_inject_count": int(kwargs.get("would_inject_count") or 0),
            "injected_count": int(kwargs.get("injected_count") or 0),
            "retrieved_token_estimate": int(kwargs.get("retrieved_token_estimate") or 0),
            "injected_token_estimate": int(kwargs.get("injected_token_estimate") or 0),
            "injected_ids_json": canonical_json(list(kwargs.get("injected_ids") or ())),
            "error_category": str(error_category)[:64] if error_category else None,
            "cold_retrieval_ms": float(
                kwargs.get("latency_ms") if cold_retrieval_ms is None else cold_retrieval_ms
            ),
            "input_preview": str(kwargs.get("input_preview") or "")[:240],
            "source_previews_json": canonical_json(
                {
                    str(key): str(value)[:240]
                    for key, value in (kwargs.get("source_previews") or {}).items()
                }
            ),
            "constructed_query": str(kwargs.get("constructed_query") or query)[:512],
            "input_terms_json": canonical_json(list(kwargs.get("input_terms") or ())[:96]),
            "identifier_terms_json": canonical_json(
                list(kwargs.get("identifier_terms") or ())[:20]
            ),
            "ordinary_terms_json": canonical_json(
                list(kwargs.get("ordinary_terms") or ())[:28]
            ),
            "fts_query": str(kwargs.get("fts_query") or "")[:2048],
            "raw_fts_candidates_json": canonical_json(
                list(kwargs.get("raw_fts_candidates") or ())[:20]
            ),
            "ranking_details_json": canonical_json(
                list(kwargs.get("ranking_details") or ())[:20]
            ),
            "query_construction_ms": float(kwargs.get("query_construction_ms") or 0.0),
            "db_checkout_ms": float(kwargs.get("db_checkout_ms") or 0.0),
            "fts_ms": float(kwargs.get("fts_ms") or 0.0),
            "ranking_ms": float(kwargs.get("ranking_ms") or 0.0),
            "token_budget_ms": float(kwargs.get("token_budget_ms") or 0.0),
            "telemetry_ms": float(kwargs.get("telemetry_ms") or 0.0),
            "total_ms": float(
                kwargs.get("total_ms")
                if kwargs.get("total_ms") is not None
                else kwargs.get("latency_ms") or 0.0
            ),
        }
        return payload

    @staticmethod
    def _insert_cold_retrieval_run(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO cold_retrieval_runs(
                id, project_id, thread_id, query, candidate_ids_json,
                scores_json, would_inject_ids_json, mode, latency_ms,
                status, error, created_at, attempted, timed_out, fail_open,
                query_hash, query_preview, exact_candidate_count,
                lexical_candidate_count, unique_candidate_count,
                threshold_candidate_count, would_inject_count,
                injected_count, retrieved_token_estimate,
                injected_token_estimate, injected_ids_json, error_category,
                cold_retrieval_ms, input_preview, source_previews_json,
                constructed_query, input_terms_json, identifier_terms_json,
                ordinary_terms_json,
                fts_query, raw_fts_candidates_json, ranking_details_json,
                query_construction_ms, db_checkout_ms, fts_ms, ranking_ms,
                token_budget_ms, telemetry_ms, total_ms
            ) VALUES (
                :id, :project_id, :thread_id, :query, :candidate_ids_json,
                :scores_json, :would_inject_ids_json, :mode, :latency_ms,
                :status, :error, :created_at, :attempted, :timed_out,
                :fail_open, :query_hash, :query_preview,
                :exact_candidate_count, :lexical_candidate_count,
                :unique_candidate_count, :threshold_candidate_count,
                :would_inject_count, :injected_count,
                :retrieved_token_estimate, :injected_token_estimate,
                :injected_ids_json, :error_category, :cold_retrieval_ms,
                :input_preview, :source_previews_json, :constructed_query,
                :input_terms_json, :identifier_terms_json,
                :ordinary_terms_json, :fts_query,
                :raw_fts_candidates_json, :ranking_details_json,
                :query_construction_ms, :db_checkout_ms, :fts_ms, :ranking_ms,
                :token_budget_ms, :telemetry_ms, :total_ms
            )
            """,
            payload,
        )

    def record_cold_telemetry_batch(
        self,
        operations: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> None:
        """Persist queued cold telemetry and reinforcement in one transaction."""

        if not operations:
            return
        with self.transaction(immediate=True) as connection:
            for operation in operations:
                kind = operation["kind"]
                if kind == "retrieval_run":
                    self._insert_cold_retrieval_run(connection, operation["payload"])
                elif kind == "memory_retrieval":
                    self._apply_memory_retrieval(
                        connection,
                        memory_ids=operation["memory_ids"],
                        injected_ids=operation["injected_ids"],
                    )
                elif kind == "run_update":
                    ids = tuple(dict.fromkeys(operation["injected_ids"]))
                    connection.execute(
                        """
                        UPDATE cold_retrieval_runs
                        SET injected_count = ?,
                            injected_token_estimate = ?,
                            injected_ids_json = ?
                        WHERE id = ?
                        """,
                        (
                            len(ids),
                            int(operation["injected_token_estimate"]),
                            canonical_json(list(ids)),
                            operation["run_id"],
                        ),
                    )
                else:
                    raise ValueError(f"unknown cold telemetry operation: {kind}")

    def update_cold_retrieval_run(
        self,
        run_id: str,
        *,
        injected_ids: tuple[str, ...] | list[str],
        injected_token_estimate: int,
    ) -> None:
        """Attach actual injection accounting to an existing sidecar run."""

        ids = tuple(dict.fromkeys(injected_ids))
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE cold_retrieval_runs
                SET injected_count = ?,
                    injected_token_estimate = ?,
                    injected_ids_json = ?
                WHERE id = ?
                """,
                (
                    len(ids),
                    int(injected_token_estimate),
                    canonical_json(list(ids)),
                    run_id,
                ),
            )

    def list_memory_evidence(self, memory_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.*
                FROM memory_evidence AS me
                JOIN events AS e ON e.id = me.event_id
                WHERE me.memory_id = ?
                ORDER BY e.sequence
                """,
                (memory_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _capsule_source_event_ids(
        self,
        connection: sqlite3.Connection,
        *,
        thread_id: str,
        covered_start_event_id: str | None,
        covered_end_event_id: str | None,
    ) -> tuple[str, ...]:
        if not covered_start_event_id or not covered_end_event_id:
            return ()
        start = connection.execute(
            "SELECT sequence FROM events WHERE id = ? AND thread_id = ?",
            (covered_start_event_id, thread_id),
        ).fetchone()
        end = connection.execute(
            "SELECT sequence FROM events WHERE id = ? AND thread_id = ?",
            (covered_end_event_id, thread_id),
        ).fetchone()
        if start is None or end is None:
            return ()
        low = min(int(start["sequence"]), int(end["sequence"]))
        high = max(int(start["sequence"]), int(end["sequence"]))
        rows = connection.execute(
            """
            SELECT id
            FROM events
            WHERE thread_id = ? AND sequence BETWEEN ? AND ?
            ORDER BY sequence
            """,
            (thread_id, low, high),
        ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def replace_capsule_sources(
        self,
        capsule_id: str,
        *,
        event_ids: tuple[str, ...],
    ) -> None:
        with self.transaction(immediate=True) as connection:
            capsule = connection.execute(
                "SELECT id FROM capsules WHERE id = ?",
                (capsule_id,),
            ).fetchone()
            if capsule is None:
                raise ValueError("capsule does not exist")
            connection.execute(
                "DELETE FROM capsule_sources WHERE capsule_id = ?",
                (capsule_id,),
            )
            for position, event_id in enumerate(event_ids):
                connection.execute(
                    """
                    INSERT INTO capsule_sources(capsule_id, event_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (capsule_id, event_id, position),
                )

    def list_capsule_sources(self, capsule_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT capsule_id, event_id, position
                FROM capsule_sources
                WHERE capsule_id = ?
                ORDER BY position
                """,
                (capsule_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_capsule(
        self,
        *,
        thread_id: str,
        base_capsule_id: str | None,
        content: str,
        source_event_hash: str,
        input_hash: str,
        output_hash: str,
        capsule_hash: str,
        snapshot_start_event_id: str | None,
        snapshot_end_event_id: str | None,
        covered_start_event_id: str | None,
        covered_end_event_id: str | None,
        model_metadata: dict[str, Any] | None = None,
        state: str = "GENERATED",
        capsule_id: str | None = None,
        source_event_ids: tuple[str, ...] | None = None,
    ) -> str:
        capsule_id = capsule_id or new_id("cap")
        with self.transaction(immediate=True) as connection:
            thread = connection.execute("SELECT id FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if thread is None:
                raise ValueError("thread does not exist")
            if base_capsule_id:
                base = connection.execute(
                    "SELECT id, thread_id FROM capsules WHERE id = ?",
                    (base_capsule_id,),
                ).fetchone()
                if base is None or base["thread_id"] != thread_id:
                    raise ValueError("base capsule does not belong to thread")
            connection.execute(
                """
                INSERT INTO capsules(
                    id, thread_id, base_capsule_id, snapshot_start_event_id,
                    snapshot_end_event_id, covered_start_event_id,
                    covered_end_event_id, source_event_hash, input_hash,
                    output_hash, capsule_hash, content, model_metadata_json,
                    state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capsule_id,
                    thread_id,
                    base_capsule_id,
                    snapshot_start_event_id,
                    snapshot_end_event_id,
                    covered_start_event_id,
                    covered_end_event_id,
                    source_event_hash,
                    input_hash,
                    output_hash,
                    capsule_hash,
                    content,
                    canonical_json(model_metadata or {}),
                    state,
                    utc_now(),
                ),
            )
            resolved_source_ids = source_event_ids
            if resolved_source_ids is None:
                resolved_source_ids = self._capsule_source_event_ids(
                    connection,
                    thread_id=thread_id,
                    covered_start_event_id=covered_start_event_id,
                    covered_end_event_id=covered_end_event_id,
                )
            for position, event_id in enumerate(resolved_source_ids):
                connection.execute(
                    """
                    INSERT INTO capsule_sources(capsule_id, event_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (capsule_id, event_id, position),
                )
        return capsule_id

    def mark_capsule_ready(self, capsule_id: str) -> bool:
        with self.transaction(immediate=True) as connection:
            capsule = connection.execute(
                "SELECT covered_end_event_id FROM capsules WHERE id = ?",
                (capsule_id,),
            ).fetchone()
            if capsule is None:
                return False
            if capsule["covered_end_event_id"]:
                source_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM capsule_sources WHERE capsule_id = ?",
                    (capsule_id,),
                ).fetchone()
                if source_count is None or int(source_count["count"]) == 0:
                    return False
            result = connection.execute(
                """
                UPDATE capsules
                SET state = 'READY', validated_at = ?
                WHERE id = ? AND state IN ('GENERATED', 'VALIDATED')
                """,
                (utc_now(), capsule_id),
            )
            return result.rowcount == 1

    def mark_capsule_failed(self, capsule_id: str, error: str) -> bool:
        with self.transaction(immediate=True) as connection:
            result = connection.execute(
                """
                UPDATE capsules
                SET state = 'FAILED', error = ?
                WHERE id = ? AND state NOT IN ('ACTIVE', 'SUPERSEDED', 'STALE')
                """,
                (error, capsule_id),
            )
            return result.rowcount == 1

    def _promote_capsule_cas_in_connection(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
        candidate_id: str,
        *,
        job_id: str | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> bool:
        if job_id is not None or worker_id is not None or lease_token is not None:
            if not all((job_id, worker_id, lease_token)):
                raise ValueError(
                    "job_id, worker_id, and lease_token must be provided together"
                )
            ownership = connection.execute(
                """
                SELECT 1
                FROM compaction_jobs
                WHERE id = ? AND worker_id = ? AND lease_token = ?
                  AND status = 'RUNNING'
                  AND lease_until IS NOT NULL AND lease_until > ?
                """,
                (job_id, worker_id, lease_token, utc_now()),
            ).fetchone()
            if ownership is None:
                connection.execute(
                    """
                    UPDATE capsules
                    SET state = 'STALE'
                    WHERE id = ? AND state = 'READY'
                    """,
                    (candidate_id,),
                )
                return False
        thread = connection.execute(
            "SELECT active_capsule_id FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        candidate = connection.execute(
            "SELECT * FROM capsules WHERE id = ? AND thread_id = ?",
            (candidate_id, thread_id),
        ).fetchone()
        if thread is None or candidate is None:
            raise ValueError("thread or candidate does not exist")
        if candidate["state"] != "READY":
            raise ValueError("only READY capsules can be promoted")
        active_id = thread["active_capsule_id"]
        if active_id == candidate_id:
            return True
        if candidate["base_capsule_id"] != active_id:
            connection.execute(
                "UPDATE capsules SET state = 'STALE' WHERE id = ? AND state = 'READY'",
                (candidate_id,),
            )
            return False

        connection.execute(
            "UPDATE capsules SET state = 'ACTIVE' WHERE id = ? AND state = 'READY'",
            (candidate_id,),
        )
        if active_id is not None:
            connection.execute(
                "UPDATE capsules SET state = 'SUPERSEDED' WHERE id = ? AND state = 'ACTIVE'",
                (active_id,),
            )
        result = connection.execute(
            """
            UPDATE threads
            SET active_capsule_id = ?
            WHERE id = ? AND active_capsule_id IS ?
            """,
            (candidate_id, thread_id, active_id),
        )
        if result.rowcount != 1:
            raise RuntimeError("capsule promotion compare-and-swap failed")
        from .coding import freeze_view
        freeze_view(connection, thread_id, candidate_id, self.coding_policy)
        return True

    def promote_capsule_cas(
        self,
        thread_id: str,
        candidate_id: str,
        *,
        job_id: str | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> bool:
        """Atomically promote a READY descendant of the current active capsule."""
        with self.transaction(immediate=True) as connection:
            return self._promote_capsule_cas_in_connection(
                connection,
                thread_id,
                candidate_id,
                job_id=job_id,
                worker_id=worker_id,
                lease_token=lease_token,
            )

    def promote_capsule_cas_and_finish_job(
        self,
        thread_id: str,
        candidate_id: str,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
    ) -> bool:
        """Commit capsule promotion and PROMOTED job completion together.

        A worker crash after the promotion commit must never leave an ACTIVE
        capsule paired with a still-RUNNING or later-recovered job. Keeping
        both writes in one immediate transaction makes the committed states
        durable as one unit; a crash before commit rolls both back.
        """
        with self.transaction(immediate=True) as connection:
            promoted = self._promote_capsule_cas_in_connection(
                connection,
                thread_id,
                candidate_id,
                job_id=job_id,
                worker_id=worker_id,
                lease_token=lease_token,
            )
            if not promoted:
                return False
            result = connection.execute(
                """
                UPDATE compaction_jobs
                SET status = 'PROMOTED', finished_at = ?, lease_until = NULL,
                    error = NULL
                WHERE id = ? AND worker_id = ? AND lease_token = ?
                  AND status = 'RUNNING'
                """,
                (utc_now(), job_id, worker_id, lease_token),
            )
            if result.rowcount != 1:
                raise RuntimeError(
                    "promoted capsule could not finish its compaction job"
                )
            return True

    def create_compaction_job(
        self,
        *,
        thread_id: str,
        base_capsule_id: str | None,
        snapshot_start_event_id: str | None,
        snapshot_end_event_id: str | None,
        priority: int = 0,
        job_id: str | None = None,
    ) -> str:
        job_id = job_id or new_id("job")
        idempotency_key = "|".join(
            [thread_id, base_capsule_id or "none", snapshot_end_event_id or "none"]
        )
        with self.transaction(immediate=True) as connection:
            generation_row = connection.execute(
                "SELECT COALESCE(MAX(generation), 0) + 1 AS generation FROM compaction_jobs WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            try:
                connection.execute(
                    """
                    INSERT INTO compaction_jobs(
                        id, thread_id, base_capsule_id, snapshot_start_event_id,
                        snapshot_end_event_id, status, priority, generation,
                        idempotency_key, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        thread_id,
                        base_capsule_id,
                        snapshot_start_event_id,
                        snapshot_end_event_id,
                        priority,
                        generation_row["generation"],
                        idempotency_key,
                        utc_now(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                if "idempotency_key" not in str(error):
                    raise
                existing = connection.execute(
                    "SELECT id, status FROM compaction_jobs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is None:
                    raise
                if existing["status"] in {"QUEUED", "RUNNING"}:
                    return str(existing["id"])
                if existing["status"] in {"FAILED", "STALE"}:
                    connection.execute(
                        "UPDATE compaction_jobs SET idempotency_key = ? WHERE id = ?",
                        (
                            f"{idempotency_key}|retired|{existing['id']}",
                            existing["id"],
                        ),
                    )
                    generation_row = connection.execute(
                        "SELECT COALESCE(MAX(generation), 0) + 1 AS generation "
                        "FROM compaction_jobs WHERE thread_id = ?",
                        (thread_id,),
                    ).fetchone()
                    connection.execute(
                        """
                        INSERT INTO compaction_jobs(
                            id, thread_id, base_capsule_id, snapshot_start_event_id,
                            snapshot_end_event_id, status, priority, generation,
                            idempotency_key, created_at
                        ) VALUES (?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?)
                        """,
                        (
                            job_id,
                            thread_id,
                            base_capsule_id,
                            snapshot_start_event_id,
                            snapshot_end_event_id,
                            priority,
                            generation_row["generation"],
                            idempotency_key,
                            utc_now(),
                        ),
                    )
                else:
                    return str(existing["id"])
        return job_id

    def request_coalesced_compaction_job(
        self,
        *,
        thread_id: str,
        base_capsule_id: str | None,
        snapshot_start_event_id: str | None,
        snapshot_end_event_id: str | None,
        priority: int = 0,
    ) -> str | None:
        """Record one current snapshot or one durable dirty indication.

        Pressure signals are frequent, but the work they request is not. A
        running job owns its immutable snapshot; later history sets the
        thread's durable dirty bit. Legacy queued snapshots are parked as
        STALE before a fresh snapshot is admitted.
        """
        if snapshot_end_event_id is None or snapshot_start_event_id is None:
            self.clear_compaction_dirty_if_idle(thread_id)
            return None
        with self.transaction(immediate=True) as connection:
            thread = connection.execute(
                "SELECT active_capsule_id FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
            if thread is None:
                raise ValueError("thread does not exist")
            all_active_jobs = connection.execute(
                """
                SELECT * FROM compaction_jobs
                WHERE thread_id = ? AND status IN ('QUEUED', 'RUNNING')
                ORDER BY CASE status WHEN 'RUNNING' THEN 0 ELSE 1 END, created_at
                """,
                (thread_id,),
            ).fetchall()
            now = utc_now()
            active_jobs = [
                row
                for row in all_active_jobs
                if row["status"] == "QUEUED"
                or row["lease_until"] is None
                or row["lease_until"] > now
            ]
            running = next((row for row in active_jobs if row["status"] == "RUNNING"), None)
            if running is not None:
                newer = (
                    running["snapshot_end_event_id"] != snapshot_end_event_id
                    or running["base_capsule_id"] != base_capsule_id
                )
                if newer:
                    connection.execute(
                        """
                        UPDATE threads
                        SET compaction_dirty = 1,
                            compaction_dirty_priority = MAX(compaction_dirty_priority, ?)
                        WHERE id = ?
                        """,
                        (priority, thread_id),
                    )
                for row in active_jobs:
                    if row["status"] == "QUEUED":
                        connection.execute(
                            """
                            UPDATE compaction_jobs
                            SET status = 'STALE', finished_at = ?,
                                error = 'coalesced behind running snapshot'
                            WHERE id = ? AND status = 'QUEUED'
                            """,
                            (utc_now(), row["id"]),
                        )
                return str(running["id"])

            matching = [
                row
                for row in active_jobs
                if row["base_capsule_id"] == base_capsule_id
                and row["snapshot_start_event_id"] == snapshot_start_event_id
                and row["snapshot_end_event_id"] == snapshot_end_event_id
            ]
            if matching:
                keep = matching[-1]
                connection.execute(
                    "UPDATE compaction_jobs SET priority = MAX(priority, ?) WHERE id = ?",
                    (priority, keep["id"]),
                )
                for row in active_jobs:
                    if row["id"] != keep["id"]:
                        connection.execute(
                            """
                            UPDATE compaction_jobs
                            SET status = 'STALE', finished_at = ?,
                                error = 'duplicate queued snapshot coalesced'
                            WHERE id = ? AND status = 'QUEUED'
                            """,
                            (utc_now(), row["id"]),
                        )
                connection.execute(
                    "UPDATE threads SET compaction_dirty = 0, compaction_dirty_priority = 0 WHERE id = ?",
                    (thread_id,),
                )
                return str(keep["id"])

            for row in active_jobs:
                connection.execute(
                    """
                    UPDATE compaction_jobs
                    SET status = 'STALE', finished_at = ?,
                        error = 'obsolete queued snapshot replaced by fresh watermark'
                    WHERE id = ? AND status = 'QUEUED'
                    """,
                    (utc_now(), row["id"]),
                )
            generation = connection.execute(
                "SELECT COALESCE(MAX(generation), 0) + 1 AS generation FROM compaction_jobs WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()["generation"]
            job_id = new_id("job")
            idempotency_key = "|".join(
                [thread_id, base_capsule_id or "none", snapshot_end_event_id]
            )
            existing = connection.execute(
                "SELECT id, status FROM compaction_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["status"] in {"QUEUED", "RUNNING"}:
                    connection.execute(
                        "UPDATE compaction_jobs SET priority = MAX(priority, ?) WHERE id = ?",
                        (priority, existing["id"]),
                    )
                    connection.execute(
                        "UPDATE threads SET compaction_dirty = 0, compaction_dirty_priority = 0 WHERE id = ?",
                        (thread_id,),
                    )
                    return str(existing["id"])
                if existing["status"] in {"FAILED", "STALE"}:
                    connection.execute(
                        "UPDATE compaction_jobs SET idempotency_key = ? WHERE id = ?",
                        (
                            f"{idempotency_key}|retired|{existing['id']}",
                            existing["id"],
                        ),
                    )
                else:
                    connection.execute(
                        "UPDATE threads SET compaction_dirty = 0, compaction_dirty_priority = 0 WHERE id = ?",
                        (thread_id,),
                    )
                    return str(existing["id"])
            connection.execute(
                """
                INSERT INTO compaction_jobs(
                    id, thread_id, base_capsule_id, snapshot_start_event_id,
                    snapshot_end_event_id, status, priority, generation,
                    idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    thread_id,
                    base_capsule_id,
                    snapshot_start_event_id,
                    snapshot_end_event_id,
                    priority,
                    generation,
                    idempotency_key,
                    utc_now(),
                ),
            )
            connection.execute(
                "UPDATE threads SET compaction_dirty = 0, compaction_dirty_priority = 0 WHERE id = ?",
                (thread_id,),
            )
            return job_id

    def get_compaction_state(self, thread_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT compaction_dirty, compaction_dirty_priority
                FROM threads WHERE id = ?
                """,
                (thread_id,),
            ).fetchone()
        if row is None:
            raise ValueError("thread does not exist")
        return dict(row)

    def clear_compaction_dirty_if_idle(self, thread_id: str) -> bool:
        with self.transaction(immediate=True) as connection:
            active = connection.execute(
                """
                SELECT 1 FROM compaction_jobs
                WHERE thread_id = ? AND status IN ('QUEUED', 'RUNNING')
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
            if active is not None:
                return False
            result = connection.execute(
                """
                UPDATE threads
                SET compaction_dirty = 0, compaction_dirty_priority = 0
                WHERE id = ? AND compaction_dirty = 1
                """,
                (thread_id,),
            )
            return result.rowcount == 1

    def claim_next_job(
        self,
        worker_id: str,
        lease_seconds: int = 900,
        *,
        recover_expired: bool = True,
    ) -> dict[str, Any] | None:
        with self.transaction(immediate=True) as connection:
            now = utc_now()
            recovery_clause = (
                " OR (status = 'RUNNING' AND lease_until IS NOT NULL "
                "AND lease_until < ?)"
                if recover_expired
                else ""
            )
            query_params: tuple[Any, ...] = (now,) if recover_expired else ()
            row = connection.execute(
                f"""
                SELECT *
                FROM compaction_jobs
                WHERE status = 'QUEUED'
                {recovery_clause}
                ORDER BY priority DESC, created_at
                LIMIT 1
                """,
                query_params,
            ).fetchone()
            if row is None:
                return None
            lease_until = datetime.fromtimestamp(
                datetime.now(UTC).timestamp() + lease_seconds,
                tz=UTC,
            ).isoformat(timespec="milliseconds")
            lease_token = uuid.uuid4().hex
            connection.execute(
                """
                UPDATE compaction_jobs
                SET status = 'RUNNING', worker_id = ?, lease_until = ?,
                    lease_token = ?, started_at = COALESCE(started_at, ?),
                    attempts = attempts + 1
                WHERE id = ?
                """,
                (worker_id, lease_until, lease_token, now, row["id"]),
            )
            updated = connection.execute(
                "SELECT * FROM compaction_jobs WHERE id = ?",
                (row["id"],),
            ).fetchone()
        return dict(updated)

    def renew_job_lease(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int = 900,
    ) -> bool:
        with self.transaction(immediate=True) as connection:
            now = utc_now()
            lease_until = datetime.fromtimestamp(
                datetime.now(UTC).timestamp() + lease_seconds,
                tz=UTC,
            ).isoformat(timespec="milliseconds")
            result = connection.execute(
                """
                UPDATE compaction_jobs
                SET lease_until = ?
                WHERE id = ? AND worker_id = ? AND lease_token = ?
                  AND status = 'RUNNING' AND lease_until > ?
                """,
                (lease_until, job_id, worker_id, lease_token, now),
            )
            return result.rowcount == 1

    def owns_job(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
    ) -> bool:
        now = utc_now()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM compaction_jobs
                WHERE id = ? AND worker_id = ? AND lease_token = ?
                  AND status = 'RUNNING'
                  AND lease_until IS NOT NULL AND lease_until > ?
                """,
                (job_id, worker_id, lease_token, now),
            ).fetchone()
        return row is not None

    def finish_job(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
        *,
        worker_id: str,
        lease_token: str,
    ) -> bool:
        if status not in {"READY", "FAILED", "STALE", "PROMOTED"}:
            raise ValueError(f"invalid terminal job status: {status}")
        with self.transaction(immediate=True) as connection:
            result = connection.execute(
                """
                UPDATE compaction_jobs
                SET status = ?, finished_at = ?, lease_until = NULL, error = ?
                WHERE id = ? AND worker_id = ? AND lease_token = ?
                  AND status IN ('RUNNING', 'QUEUED')
                """,
                (status, utc_now(), error, job_id, worker_id, lease_token),
            )
            return result.rowcount == 1

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM compaction_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def record_model_run(self, record: dict[str, Any]) -> str:
        run_id = str(record.get("id") or new_id("run"))
        generation_settings = record.get("generation_settings") or {}
        source_refs = record.get("source_refs") or ()
        metadata = record.get("metadata") or {}
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO model_runs(
                    id, job_id, thread_id, generation, pass_name, stage,
                    selector_chunk_index, canonicalizer_batch_index, model,
                    endpoint, prompt_version, generation_settings_json,
                    source_refs_json, input_hash, output_hash, raw_response_hash,
                    diagnostic_excerpt, reasoning_tokens, input_tokens,
                    output_tokens, wall_ms, finish_reason, status, metadata_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    record.get("job_id"),
                    record.get("thread_id"),
                    record.get("generation"),
                    record["stage"],
                    record["stage"],
                    record.get("selector_chunk_index"),
                    record.get("canonicalizer_batch_index"),
                    record["model"],
                    record.get("endpoint", ""),
                    record.get("prompt_version", ""),
                    canonical_json(generation_settings),
                    canonical_json(list(source_refs)),
                    record["input_hash"],
                    record.get("output_hash"),
                    record.get("raw_response_hash"),
                    record.get("diagnostic_excerpt"),
                    record.get("reasoning_tokens"),
                    record.get("input_tokens"),
                    record.get("output_tokens"),
                    record.get("wall_ms"),
                    record.get("finish_reason"),
                    record["status"],
                    canonical_json(metadata),
                    record.get("created_at") or utc_now(),
                ),
            )
        return run_id

    def lookup_stage_result(
        self,
        *,
        stage: str,
        model: str,
        prompt_version: str,
        input_hash: str,
        schema_hash: str,
        settings_hash: str,
        system_prompt_hash: str,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT output_json
                FROM stage_result_cache
                WHERE stage = ? AND model = ? AND prompt_version = ?
                  AND input_hash = ? AND schema_hash = ? AND settings_hash = ?
                  AND system_prompt_hash = ?
                """,
                (
                    stage,
                    model,
                    prompt_version,
                    input_hash,
                    schema_hash,
                    settings_hash,
                    system_prompt_hash,
                ),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["output_json"])
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def store_stage_result(
        self,
        *,
        stage: str,
        model: str,
        prompt_version: str,
        input_hash: str,
        schema_hash: str,
        settings_hash: str,
        system_prompt_hash: str,
        output: dict[str, Any],
    ) -> None:
        cache_key = content_hash(
            canonical_json(
                {
                    "stage": stage,
                    "model": model,
                    "prompt_version": prompt_version,
                    "input_hash": input_hash,
                    "schema_hash": schema_hash,
                    "settings_hash": settings_hash,
                    "system_prompt_hash": system_prompt_hash,
                }
            )
        )
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO stage_result_cache(
                    cache_key, stage, model, prompt_version, input_hash,
                    schema_hash, settings_hash, system_prompt_hash, output_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    stage,
                    model,
                    prompt_version,
                    input_hash,
                    schema_hash,
                    settings_hash,
                    system_prompt_hash,
                    canonical_json(output),
                    utc_now(),
                ),
            )

    def update_model_run(self, run_id: str, **updates: Any) -> None:
        allowed = {
            "status",
            "error",
            "diagnostic_excerpt",
            "output_hash",
            "raw_response_hash",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "wall_ms",
            "finish_reason",
            "error",
        }
        values = {key: value for key, value in updates.items() if key in allowed}
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.transaction(immediate=True) as connection:
            connection.execute(
                f"UPDATE model_runs SET {assignments} WHERE id = ?",
                (*values.values(), run_id),
            )

    def list_model_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if job_id is not None:
            clauses.append("job_id = ?")
            params.append(job_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM model_runs
                {where}
                ORDER BY created_at, id
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_jobs(
        self,
        *,
        thread_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if thread_id is not None:
            clauses.append("thread_id = ?")
            params.append(thread_id)
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM compaction_jobs
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]
