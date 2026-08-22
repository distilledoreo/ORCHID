from __future__ import annotations

import threading
import time
from queue import Empty, Full, Queue
from typing import Any, Protocol

from .db import SQLiteStore


class ColdMemoryTelemetrySink(Protocol):
    """Non-authoritative sink for cold-memory telemetry and reinforcement."""

    def record_cold_retrieval_run(self, **kwargs: Any) -> str | None:
        ...

    def record_memory_retrieval(
        self,
        *,
        memory_ids: tuple[str, ...] | list[str],
        injected_ids: tuple[str, ...] | list[str] = (),
    ) -> bool:
        ...

    def update_cold_retrieval_run(
        self,
        run_id: str,
        *,
        injected_ids: tuple[str, ...] | list[str],
        injected_token_estimate: int,
    ) -> bool:
        ...


class BufferedColdMemoryTelemetry:
    """Bounded, fail-open cold telemetry writer.

    Producers only prepare bounded payloads and call ``put_nowait``. A daemon
    thread drains FIFO operations into short SQLite transactions containing
    multiple operations. Telemetry failures and queue overflow are observable
    through local counters but never block retrieval or context assembly.
    """

    def __init__(
        self,
        store: SQLiteStore,
        *,
        max_queue_size: int = 256,
        batch_size: int = 32,
        flush_interval_ms: float = 5.0,
    ) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be positive")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if flush_interval_ms <= 0:
            raise ValueError("flush_interval_ms must be positive")
        self.store = store
        self.max_queue_size = max_queue_size
        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_ms / 1000
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=max_queue_size)
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._accepted_count = 0
        self._flushed_operation_count = 0
        self._dropped_count = 0
        self._flush_error_count = 0
        self._counter_lock = threading.Lock()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._stop.is_set():
                raise RuntimeError("cold telemetry writer cannot restart after close")
            self._thread = threading.Thread(
                target=self._run,
                name="orchid-cold-telemetry",
                daemon=True,
            )
            self._thread.start()

    def close(self, *, timeout_seconds: float = 2.0) -> None:
        """Stop after draining queued work, bounded by ``timeout_seconds``."""

        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, timeout_seconds))

    def metrics(self) -> dict[str, int]:
        with self._counter_lock:
            return {
                "queue_depth": self._queue.qsize(),
                "accepted_count": self._accepted_count,
                "flushed_operation_count": self._flushed_operation_count,
                "dropped_count": self._dropped_count,
                "flush_error_count": self._flush_error_count,
            }

    def record_cold_retrieval_run(self, **kwargs: Any) -> str | None:
        payload = self.store.prepare_cold_retrieval_run(**kwargs)
        run_id = str(payload["id"])
        if self._enqueue({"kind": "retrieval_run", "payload": payload}):
            return run_id
        return None

    def record_memory_retrieval(
        self,
        *,
        memory_ids: tuple[str, ...] | list[str],
        injected_ids: tuple[str, ...] | list[str] = (),
    ) -> bool:
        return self._enqueue(
            {
                "kind": "memory_retrieval",
                "memory_ids": tuple(dict.fromkeys(memory_ids)),
                "injected_ids": tuple(dict.fromkeys(injected_ids)),
            }
        )

    def update_cold_retrieval_run(
        self,
        run_id: str,
        *,
        injected_ids: tuple[str, ...] | list[str],
        injected_token_estimate: int,
    ) -> bool:
        return self._enqueue(
            {
                "kind": "run_update",
                "run_id": run_id,
                "injected_ids": tuple(dict.fromkeys(injected_ids)),
                "injected_token_estimate": int(injected_token_estimate),
            }
        )

    def _enqueue(self, operation: dict[str, Any]) -> bool:
        self.start()
        try:
            self._queue.put_nowait(operation)
        except Full:
            with self._counter_lock:
                self._dropped_count += 1
            return False
        with self._counter_lock:
            self._accepted_count += 1
        return True

    def _run(self) -> None:
        while True:
            if self._stop.is_set() and self._queue.empty():
                return
            try:
                first = self._queue.get(timeout=self.flush_interval_seconds)
            except Empty:
                continue
            batch = [first]
            deadline = time.monotonic() + self.flush_interval_seconds
            while len(batch) < self.batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(self._queue.get(timeout=remaining))
                except Empty:
                    break
            try:
                self.store.record_cold_telemetry_batch(batch)
            except Exception:
                with self._counter_lock:
                    self._flush_error_count += len(batch)
            else:
                with self._counter_lock:
                    self._flushed_operation_count += len(batch)
            finally:
                for _ in batch:
                    self._queue.task_done()
