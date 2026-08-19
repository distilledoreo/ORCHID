from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

from .compaction import (
    Capsule,
    CompactionResult,
    Event,
    compute_input_hash,
)


def _output_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class PerfectCompactionEngine:
    model_identity: str = "fake-perfect"
    prompt_version: str = "fake-v1"

    async def compact(
        self,
        *,
        base_capsule: Capsule | None,
        events: list[Event],
        snapshot_end_event_id: str | None,
    ) -> CompactionResult:
        event_text = "\n".join(f"{event.id}: {event.content}" for event in events)
        content = "\n".join(part for part in [base_capsule.content if base_capsule else "", event_text] if part)
        content = content or "empty durable capsule"
        covered = tuple(event.id for event in events)
        return CompactionResult(
            content=content,
            covered_event_ids=covered,
            evidence_event_ids=covered,
            input_hash=compute_input_hash(base_capsule, events, snapshot_end_event_id),
            output_hash=_output_hash(content),
            model_identity=self.model_identity,
            prompt_version=self.prompt_version,
            generation_settings={"temperature": 0, "seed": 0},
        )


@dataclass
class BrokenCompactionEngine:
    reason: str = "missing frozen-event coverage"

    async def compact(
        self,
        *,
        base_capsule: Capsule | None,
        events: list[Event],
        snapshot_end_event_id: str | None,
    ) -> CompactionResult:
        content = f"broken candidate: {self.reason}"
        covered = tuple(event.id for event in events[:-1])
        return CompactionResult(
            content=content,
            covered_event_ids=covered,
            evidence_event_ids=("unknown-event",),
            input_hash=compute_input_hash(base_capsule, events, snapshot_end_event_id),
            output_hash=_output_hash(content),
            model_identity="fake-broken",
            prompt_version="fake-v1",
            generation_settings={},
        )


@dataclass
class SlowCompactionEngine:
    delay_seconds: float = 0.05

    async def compact(
        self,
        *,
        base_capsule: Capsule | None,
        events: list[Event],
        snapshot_end_event_id: str | None,
    ) -> CompactionResult:
        await asyncio.sleep(self.delay_seconds)
        return await PerfectCompactionEngine(model_identity="fake-slow").compact(
            base_capsule=base_capsule,
            events=events,
            snapshot_end_event_id=snapshot_end_event_id,
        )
