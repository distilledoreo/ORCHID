from memory_gateway.compaction import Event
from memory_gateway.db import SQLiteStore, content_hash
from memory_gateway.ingestion import append_novel_messages
from memory_gateway.source_projection import (
    classify_tool_text,
    project_compaction_events,
    project_tool_content,
    tool_inner_text,
)


def _tool_event(content: str, sequence: int = 1) -> Event:
    payload = '{"content":%s,"role":"tool","tool_call_id":"call_1"}' % (
        __import__("json").dumps(content)
    )
    return Event(
        id=f"evt_{sequence}",
        sequence=sequence,
        content=payload,
        content_hash=content_hash(payload),
        event_type="tool_result",
        role="tool",
    )


def test_classifies_source_file_and_path_list() -> None:
    source = "import { foo } from './bar.js';\n" + ("export const x = 1;\n" * 40)
    assert classify_tool_text(source) == "source_file"
    paths = "\n".join(f"/repo/pkg/file_{index}.ts" for index in range(20))
    assert classify_tool_text(paths) == "path_list"
    assert classify_tool_text("error TS6310: Referenced project missing") == "diagnostics"
    assert classify_tool_text("wrote 12 bytes") == "keep"


def test_projects_file_read_to_stub_and_keeps_ids() -> None:
    source = "import { Layer } from './types.js';\n" + ("export type X = number;\n" * 80)
    event = _tool_event(source)
    projected = project_compaction_events([event])[0]
    assert projected.id == event.id
    assert projected.content.startswith("TOOL_READ_STUB")
    assert "import { Layer }" in projected.content
    assert len(projected.content) < len(event.content) // 4


def test_duplicate_tool_results_collapse_inside_snapshot() -> None:
    source = "import { Layer } from './types.js';\n" + ("export type X = number;\n" * 80)
    first = _tool_event(source, 1)
    second = Event(
        id="evt_2",
        sequence=2,
        content=first.content,
        content_hash=first.content_hash,
        event_type="tool_result",
        role="tool",
    )
    projected = project_compaction_events([first, second])
    assert projected[0].content.startswith("TOOL_READ_STUB")
    assert projected[1].content.startswith("DUPLICATE_TOOL_RESULT")
    assert "first_sequence=1" in projected[1].content


def test_diagnostics_keep_error_lines() -> None:
    text = "something fine\nerror TS6310: broken ref\nmore noise\n"
    kind, stub = project_tool_content(
        __import__("json").dumps({"content": text, "role": "tool"}),
        content_hash_value="abc",
    )
    assert kind == "diagnostics"
    assert "error TS6310" in stub
    assert "something fine" not in stub


def test_ingestion_skips_resent_tool_history_when_prefix_does_not_match(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    tool = {"role": "tool", "tool_call_id": "c", "content": "FILEBODY"}
    append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            tool,
        ],
        request_id="r1",
        source="request",
    )
    resent = append_novel_messages(
        store,
        project_id="project",
        thread_id="thread",
        messages=[
            {"role": "system", "content": "new-system"},
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            tool,
            {"role": "assistant", "content": "D"},
        ],
        request_id="r2",
        source="request",
    )
    assert resent.skipped_duplicate_count == 3
    assert [event["role"] for event in resent.appended_events] == ["system", "assistant"]
    assert len(store.list_events("thread")) == 5


def test_inner_text_unwraps_pi_tool_envelope() -> None:
    payload = '{"content":"hello file","role":"tool","tool_call_id":"x"}'
    assert tool_inner_text(payload) == "hello file"
