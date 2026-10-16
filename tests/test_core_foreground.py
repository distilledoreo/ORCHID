from __future__ import annotations

import hashlib
from pathlib import Path

from memory_gateway.config import RuntimeConfig
from memory_gateway.context import ContextAssembler
from memory_gateway.core_goal import (
    FOREGROUND_EMERGENCY_CEILING_TOKENS,
    FOREGROUND_TARGET_TOKENS,
    MINIMUM_HISTORY_MULTIPLE,
)
from memory_gateway.db import SQLiteStore
from memory_gateway.ingestion import append_novel_messages

WINDOW_MESSAGES = 16
MESSAGE_CHARS = 8_000
HOT_MESSAGES = 4


def test_runtime_defaults_match_the_sealed_foreground_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ORCHID_FOREGROUND_TARGET_TOKENS", raising=False)
    monkeypatch.delenv("ORCHID_FOREGROUND_HARD_LIMIT_TOKENS", raising=False)
    config = RuntimeConfig.from_env(db_path=str(tmp_path / "memory.db"))
    assert config.foreground_target_tokens == FOREGROUND_TARGET_TOKENS
    assert (
        config.foreground_hard_limit_tokens
        == FOREGROUND_EMERGENCY_CEILING_TOKENS
    )


def _promote_through_hot_boundary(
    store: SQLiteStore,
    *,
    generation: int,
) -> None:
    events = store.list_events("thread")
    end = events[-HOT_MESSAGES - 1]
    active = store.get_active_capsule("thread")
    if active is None:
        start = events[0]
        base_id = None
    else:
        covered = store.get_event(active["covered_end_event_id"])
        assert covered is not None
        start = next(
            event
            for event in events
            if int(event["sequence"]) == int(covered["sequence"]) + 1
        )
        base_id = active["id"]
    content = f"## State\n- generation {generation}; raw journal remains authoritative"
    digest = hashlib.sha256(content.encode()).hexdigest()
    capsule_id = store.create_capsule(
        thread_id="thread",
        base_capsule_id=base_id,
        content=content,
        source_event_hash=digest,
        input_hash=f"input-{generation}",
        output_hash=digest,
        capsule_hash=digest,
        snapshot_start_event_id=start["id"],
        snapshot_end_event_id=end["id"],
        covered_start_event_id=start["id"],
        covered_end_event_id=end["id"],
    )
    assert store.mark_capsule_ready(capsule_id)
    assert store.promote_capsule_cas("thread", capsule_id)


def test_six_window_history_keeps_foreground_bounded_without_compaction_wait(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    assembler = ContextAssembler(
        store,
        raw_tail_target_tokens=8_000,
        minimum_raw_tail_tokens=8_000,
        context_budget_tokens=FOREGROUND_TARGET_TOKENS,
        foreground_hard_limit_tokens=FOREGROUND_EMERGENCY_CEILING_TOKENS,
    )
    messages: list[dict[str, str]] = []
    prompt_sizes: list[int] = []

    for generation in range(1, MINIMUM_HISTORY_MULTIPLE + 2):
        for index in range(WINDOW_MESSAGES):
            marker = f"generation={generation} message={index} "
            messages.append(
                {
                    "role": "user",
                    "content": marker + ("x" * (MESSAGE_CHARS - len(marker))),
                }
            )
        append_novel_messages(
            store,
            project_id="project",
            thread_id="thread",
            messages=messages,
            request_id=f"request-{generation}",
            source="request",
        )
        _promote_through_hot_boundary(store, generation=generation)

        snapshot = assembler.assemble("thread", messages, project_id="project")
        prompt_sizes.append(snapshot.estimated_tokens)
        assert snapshot.estimated_tokens <= FOREGROUND_TARGET_TOKENS
        assert not snapshot.target_exceeded
        assert not snapshot.hard_limit_exceeded
        assert snapshot.emergency_spill_tokens == 0
        assert snapshot.compaction_wait_ms == 0
        assert snapshot.memory_maintenance_awaited is False
        assert snapshot.organizer_lag_events == HOT_MESSAGES
        assert sum(snapshot.component_tokens.values()) == snapshot.estimated_tokens

    events = store.list_events("thread")
    history_tokens = sum(int(event["token_count"]) for event in events)
    assert history_tokens > FOREGROUND_TARGET_TOKENS * MINIMUM_HISTORY_MULTIPLE
    assert max(prompt_sizes) <= FOREGROUND_TARGET_TOKENS
    assert len(events) == len(messages)
    for event in events:
        persisted = store.get_event(event["id"])
        assert persisted is not None
        assert persisted["content_hash"] == hashlib.sha256(
            persisted["content"].encode()
        ).hexdigest()
