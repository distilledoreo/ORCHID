from __future__ import annotations

from dataclasses import dataclass

from .compaction import queue_snapshot_job
from .context import estimate_tokens
from .db import SQLiteStore


@dataclass(frozen=True)
class ThresholdPolicy:
    usable_context_tokens: int
    background_fraction: float = 0.65
    urgent_fraction: float = 0.85

    def priority_for(self, estimated_tokens: int) -> int | None:
        fraction = estimated_tokens / self.usable_context_tokens
        if fraction >= self.urgent_fraction:
            return 100
        if fraction >= self.background_fraction:
            return 50
        return None


class CompactionScheduler:
    """Creates immutable-watermark jobs without running model inference."""

    def __init__(self, store: SQLiteStore, policy: ThresholdPolicy):
        self.store = store
        self.policy = policy

    def uncompacted_tokens(self, thread_id: str) -> int:
        active = self.store.get_active_capsule(thread_id)
        start_sequence = None
        if active and active["covered_end_event_id"]:
            covered = self.store.get_event(active["covered_end_event_id"])
            if covered is None:
                raise ValueError("active capsule covered_end_event_id does not exist")
            if covered["thread_id"] != thread_id:
                raise ValueError("active capsule coverage belongs to another thread")
            start_sequence = covered["sequence"] + 1

        events = self.store.list_events(thread_id, start_sequence=start_sequence)
        return sum(
            event["token_count"]
            if event["token_count"] is not None
            else estimate_tokens(event["content"])
            for event in events
        )

    def maybe_enqueue(self, thread_id: str) -> str | None:
        priority = self.policy.priority_for(self.uncompacted_tokens(thread_id))
        if priority is None:
            self.store.clear_compaction_dirty_if_idle(thread_id)
            return None
        return queue_snapshot_job(self.store, thread_id, priority=priority)

    def reconcile_after_job(self, thread_id: str) -> str | None:
        return self.maybe_enqueue(thread_id)
