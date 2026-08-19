from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


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

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class SQLiteStore:
    """Small SQLite repository with explicit transaction boundaries."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_compaction_jobs(connection)
            self._migrate_model_runs(connection)

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

    def create_project(self, project_id: str, name: str | None = None) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO projects(id, name, created_at) VALUES (?, ?, ?)",
                (project_id, name or project_id, utc_now()),
            )

    def create_thread(self, thread_id: str, project_id: str) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO threads(id, project_id, created_at) VALUES (?, ?, ?)",
                (thread_id, project_id, utc_now()),
            )

    def ensure_project_and_thread(self, project_id: str, thread_id: str) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO projects(id, name, created_at) VALUES (?, ?, ?)",
                (project_id, project_id, utc_now()),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO threads(id, project_id, created_at)
                VALUES (?, ?, ?)
                """,
                (thread_id, project_id, utc_now()),
            )

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
            connection.execute(
                "UPDATE threads SET next_sequence = ? WHERE id = ?",
                (sequence + 1, thread_id),
            )
            return event

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
        return capsule_id

    def mark_capsule_ready(self, capsule_id: str) -> bool:
        with self.transaction(immediate=True) as connection:
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
                row = connection.execute(
                    "SELECT id FROM compaction_jobs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if row is None:
                    raise
                return str(row["id"])
        return job_id

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
                datetime.now(timezone.utc).timestamp() + lease_seconds,
                tz=timezone.utc,
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
                datetime.now(timezone.utc).timestamp() + lease_seconds,
                tz=timezone.utc,
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