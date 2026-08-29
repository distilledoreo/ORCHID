from __future__ import annotations

from pathlib import Path

from memory_gateway.context import ContextAssembler
from memory_gateway.db import SQLiteStore, _ordered_history_overlap_span
from memory_gateway.ingestion import append_novel_messages, event_to_message


def test_overlap_span_handles_prepended_control_messages() -> None:
    known = ["a", "b", "c"]
    incoming = ["system", "a", "b", "c", "d"]
    start, length = _ordered_history_overlap_span(known, incoming)
    assert (start, length) == (1, 3)


def test_repeated_identical_message_after_overlap_is_appended(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    first = append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[{"role": "user", "content": "same text"}],
        request_id="r1",
        source="request",
    )
    assert len(first.appended_events) == 1
    second = append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[
            {"role": "user", "content": "same text"},
            {"role": "user", "content": "same text"},
        ],
        request_id="r2",
        source="request",
    )
    assert second.reused_prefix_count == 1
    assert len(second.appended_events) == 1
    assert len(store.list_events("thread")) == 2


def test_root_task_is_persisted_and_rendered_once(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    goal = [{"role": "user", "content": "implement clone stamp"}]
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=goal,
        request_id="r1",
        source="request",
    )
    root = store.get_root_task_event("thread")
    assert root is not None
    parsed = event_to_message(root)
    assert parsed is not None
    assert parsed["content"] == goal[0]["content"]
    snapshot = ContextAssembler(store, minimum_raw_tail_tokens=1).assemble(
        "thread",
        goal + [{"role": "assistant", "content": "ack"}],
    )
    user_messages = [message for message in snapshot.messages if message.get("role") == "user"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == "implement clone stamp"
