from __future__ import annotations

import hashlib
from pathlib import Path

from memory_gateway.context import ContextAssembler, estimate_tokens
from memory_gateway.db import SQLiteStore
from memory_gateway.ingestion import append_novel_messages


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    return store


def _ingest(store: SQLiteStore, messages: list[dict], request_id: str = "r1") -> None:
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=messages,
        request_id=request_id,
        source="request",
    )


def _promote_through(store: SQLiteStore, event_id: str, content: str = "active facts") -> None:
    digest = hashlib.sha256(content.encode()).hexdigest()
    events = store.list_events("thread")
    start_id = events[0]["id"]
    capsule_id = store.create_capsule(
        thread_id="thread",
        base_capsule_id=None,
        content=content,
        source_event_hash="source",
        input_hash="input",
        output_hash=digest,
        capsule_hash=digest,
        snapshot_start_event_id=start_id,
        snapshot_end_event_id=event_id,
        covered_start_event_id=start_id,
        covered_end_event_id=event_id,
    )
    assert store.mark_capsule_ready(capsule_id)
    assert store.promote_capsule_cas("thread", capsule_id)


def _assembler(store: SQLiteStore, minimum: int = 12_000) -> ContextAssembler:
    return ContextAssembler(
        store,
        raw_tail_target_tokens=16_000,
        minimum_raw_tail_tokens=minimum,
    )


def test_no_active_still_assembles_raw_conversation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    _ingest(store, messages)
    snapshot = _assembler(store, minimum=1).assemble("thread", messages)
    assert snapshot.substitution is not None
    assert snapshot.substitution.active is False
    assert snapshot.substitution.suppressed_covered_messages == 0
    user_contents = [message["content"] for message in snapshot.messages if message.get("role") == "user"]
    assert "hello" in user_contents


def test_active_suppresses_covered_user_assistant_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    history = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
    ]
    _ingest(store, history)
    events = store.list_events("thread")
    _promote_through(store, events[1]["id"], "old Q/A summarized")
    snapshot = _assembler(store, minimum=1).assemble("thread", history)
    forwarded = [message.get("content") for message in snapshot.messages]
    assert "old question" not in forwarded
    assert "old answer" not in forwarded
    assert "new question" in forwarded
    assert snapshot.substitution.suppressed_covered_messages == 2
    assert snapshot.substitution.covered_raw_tokens_forwarded == 0


def test_new_user_message_after_active_is_preserved_verbatim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = [{"role": "user", "content": "covered user"}]
    _ingest(store, first, "r1")
    _promote_through(store, store.list_events("thread")[-1]["id"])
    current = first + [{"role": "user", "content": "exact new instruction"}]
    _ingest(store, current, "r2")
    snapshot = _assembler(store, minimum=1).assemble("thread", current)
    assert any(message.get("content") == "exact new instruction" for message in snapshot.messages)
    assert snapshot.substitution.suppressed_covered_messages == 1


def test_recent_tool_pair_after_active_is_preserved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    covered = [{"role": "user", "content": "start"}]
    _ingest(store, covered, "r1")
    _promote_through(store, store.list_events("thread")[-1]["id"])
    live = covered + [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_new",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{\"path\":\"a.ts\"}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_new", "content": "file body"},
    ]
    _ingest(store, live, "r2")
    snapshot = _assembler(store, minimum=1).assemble("thread", live)
    roles = [message.get("role") for message in snapshot.messages if message.get("role") in {"assistant", "tool"}]
    assert roles[-2:] == ["assistant", "tool"]
    assert snapshot.messages[-1]["content"] == "file body"


def test_old_tool_pair_covered_by_active_is_omitted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    history = [
        {"role": "user", "content": "please read"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_old",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_old", "content": "obsolete tool text"},
        {"role": "user", "content": "thanks, continue"},
    ]
    _ingest(store, history)
    events = store.list_events("thread")
    _promote_through(store, events[2]["id"], "old tool loop summarized")
    snapshot = _assembler(store, minimum=1).assemble("thread", history)
    contents = [message.get("content") for message in snapshot.messages]
    assert "obsolete tool text" not in contents
    assert "thanks, continue" in contents


def test_watermark_in_middle_of_tool_loop_keeps_protocol_pair(tmp_path: Path) -> None:
    store = _store(tmp_path)
    history = [
        {"role": "user", "content": "do work"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_split",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_split", "content": "tool result after watermark"},
    ]
    _ingest(store, history)
    events = store.list_events("thread")
    _promote_through(store, events[1]["id"], "assistant tool call summarized")
    snapshot = _assembler(store, minimum=1).assemble("thread", history)
    assert snapshot.substitution is not None
    assert snapshot.substitution.protocol_continuity_messages == 1
    assert snapshot.substitution.covered_raw_tokens_forwarded > 0
    tool = next(message for message in snapshot.messages if message.get("role") == "tool")
    assistant = next(
        message
        for message in snapshot.messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assert tool["content"] == "tool result after watermark"
    assert assistant["tool_calls"][0]["id"] == "call_split"


def test_repeated_identical_text_as_new_event_is_not_dropped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = [{"role": "user", "content": "same text"}]
    _ingest(store, first, "r1")
    _promote_through(store, store.list_events("thread")[-1]["id"])
    repeated = first + [{"role": "user", "content": "same text"}]
    _ingest(store, repeated, "r2")
    snapshot = _assembler(store, minimum=1).assemble("thread", repeated)
    user_messages = [message for message in snapshot.messages if message.get("role") == "user"]
    assert sum(1 for message in user_messages if message.get("content") == "same text") == 1
    assert snapshot.substitution.retained_request_messages >= 1


def test_stream_failure_follow_up_preserves_current_continuation_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    covered = [{"role": "user", "content": "old"}]
    _ingest(store, covered, "r1")
    _promote_through(store, store.list_events("thread")[-1]["id"])
    continuation = covered + [
        {"role": "assistant", "content": "partial before disconnect"},
        {"role": "user", "content": "continue from the failed stream"},
    ]
    _ingest(store, continuation, "r2")
    snapshot = _assembler(store, minimum=1).assemble("thread", continuation)
    contents = [message.get("content") for message in snapshot.messages]
    assert "continue from the failed stream" in contents
    assert "partial before disconnect" in contents
    assert "old" not in contents


def test_tail_below_historical_floor_does_not_pull_covered_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    covered = [{"role": "user", "content": "x" * 400}]
    _ingest(store, covered, "r1")
    _promote_through(store, store.list_events("thread")[-1]["id"])
    fresh = covered + [{"role": "user", "content": "tiny"}]
    _ingest(store, fresh, "r2")
    snapshot = ContextAssembler(
        store,
        raw_tail_target_tokens=16_000,
        minimum_raw_tail_tokens=12_000,
    ).assemble("thread", fresh)
    covered_event_id = store.list_events("thread")[0]["id"]
    assert covered_event_id not in snapshot.raw_tail_event_ids
    assert snapshot.substitution.covered_raw_tokens_forwarded == 0


def test_no_duplicate_representation_of_covered_events(tmp_path: Path) -> None:
    store = _store(tmp_path)
    history = [
        {"role": "user", "content": "covered payload UNIQUE_MARKER_123"},
        {"role": "assistant", "content": "ack"},
        {"role": "user", "content": "later"},
    ]
    _ingest(store, history)
    events = store.list_events("thread")
    _promote_through(store, events[1]["id"], "summary of the first exchange")
    snapshot = _assembler(store, minimum=1).assemble("thread", history)
    serialized = "\n".join(str(message.get("content", "")) for message in snapshot.messages)
    assert "UNIQUE_MARKER_123" not in serialized
    assert snapshot.substitution.covered_raw_tokens_forwarded == 0


def test_estimated_tokens_exclude_suppressed_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bulky = [{"role": "user", "content": "b" * 8000}]
    _ingest(store, bulky, "r1")
    _promote_through(store, store.list_events("thread")[-1]["id"], "short")
    current = bulky + [{"role": "user", "content": "now"}]
    _ingest(store, current, "r2")
    snapshot = _assembler(store, minimum=1).assemble("thread", current)
    assert snapshot.estimated_tokens < snapshot.substitution.inbound_request_tokens
    assert snapshot.substitution.suppressed_covered_tokens > 0
