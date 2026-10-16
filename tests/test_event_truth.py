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


def test_best_overlap_reuses_client_history_after_duplicate_snowball() -> None:
    from memory_gateway.db import _best_ordered_history_overlap_span

    known = ["stream-b", "a", "b", "c"] + ["a", "b", "c"] * 6
    incoming = ["a", "b", "c", "d"]
    start, length = _best_ordered_history_overlap_span(known, incoming)
    assert (start, length) == (0, 3)


def test_resent_full_history_after_duplicates_appends_only_suffix(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    stream = [
        {"role": "user", "content": "A"},
        {"role": "assistant", "content": "stream-B"},
    ]
    client = [
        {"role": "user", "content": "A"},
        {"role": "assistant", "content": "client-B"},
        {"role": "tool", "tool_call_id": "c", "content": "C"},
    ]
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=stream,
        request_id="r1",
        source="request",
    )
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=client,
        request_id="r2",
        source="request",
    )
    for index in range(5):
        append_novel_messages(
            store,
            project_id="project",
            thread_id="thread",
            messages=[*client, {"role": "assistant", "content": "D"}],
            request_id=f"r-dup-{index}",
            source="request",
        )
    before = len(store.list_events("thread"))
    resent = append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[*client, {"role": "assistant", "content": "D"}, {"role": "user", "content": "E"}],
        request_id="r-final",
        source="request",
    )
    assert len(resent.appended_events) == 1
    assert resent.appended_events[0]["role"] == "user"
    assert len(store.list_events("thread")) == before + 1


def test_unmatched_middle_message_does_not_reappend_known_suffix(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    history = [
        {"role": "user", "content": "A"},
        {"role": "assistant", "content": "B"},
        {"role": "tool", "tool_call_id": "c", "content": "C"},
        {"role": "assistant", "content": "D"},
    ]
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=history,
        request_id="r1",
        source="request",
    )
    gapped = [
        history[0],
        {"role": "assistant", "content": "stream-mid"},
        *history[1:],
        {"role": "user", "content": "E"},
    ]
    result = append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=gapped,
        request_id="r2",
        source="request",
    )
    assert len(result.appended_events) == 2
    assert result.appended_events[0]["role"] == "assistant"
    assert result.appended_events[1]["role"] == "user"
    assert len(store.list_events("thread")) == 6


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


def test_identical_assistant_responses_are_distinct_by_request_id(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    user_one = {"role": "user", "content": "user one"}
    user_two = {"role": "user", "content": "user two"}
    acknowledgement = {"role": "assistant", "content": "ack"}

    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[user_one],
        request_id="request-1",
        source="request",
    )
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[acknowledgement],
        request_id="request-1",
        source="response",
    )
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[user_one, acknowledgement, user_two],
        request_id="request-2",
        source="request",
    )
    second_response = append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[acknowledgement],
        request_id="request-2",
        source="response",
    )

    assert len(second_response.appended_events) == 1
    assert len(store.list_events("thread")) == 4
    replay = append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[acknowledgement],
        request_id="request-2",
        source="response",
    )
    assert replay.skipped_duplicate_count == 1
    assert len(replay.appended_events) == 0
    assert len(store.list_events("thread")) == 4


def test_exact_identifier_survives_ingestion_and_source_projection(tmp_path: Path) -> None:
    from memory_gateway.compaction import Event
    from memory_gateway.source_projection import project_compaction_events

    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    identifier = "ORC-VIA-7F3A9C2E"
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[{"role": "user", "content": f"retain {identifier} exactly"}],
        request_id="request-identifier",
        source="request",
    )
    row = store.latest_event("thread")
    assert row is not None
    event = Event.from_row(row)
    projected = project_compaction_events([event])
    assert projected[0].content == event.content
    assert identifier in projected[0].content


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
