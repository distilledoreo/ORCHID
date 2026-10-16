from memory_gateway.compaction import Event, compute_input_hash, freeze_snapshot
from memory_gateway.db import SQLiteStore, content_hash
from memory_gateway.ingestion import append_novel_messages
from memory_gateway.source_projection import (
    authoritative_source_text,
    classify_tool_text,
    exact_citation_excerpt,
    normalize_citation_match_text,
    project_compaction_events,
    project_tool_content,
    tool_inner_text,
)


def _tool_event(content: str, sequence: int = 1) -> Event:
    payload = __import__("json").dumps(
        {"content": content, "role": "tool", "tool_call_id": "call_1"}
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


def test_inner_text_unwraps_pi_text_blocks() -> None:
    payload = '{"content":[{"text":"line one\\nline two"}]}'
    assert tool_inner_text(payload) == "line one\nline two"


def test_inner_text_unwraps_tool_output_blocks() -> None:
    payload = '{"output":[{"text":"Script completed"},{"text":"Exit code: 0\\n51 passed"}]}'
    assert tool_inner_text(payload) == "Script completed\nExit code: 0\n51 passed"


def test_normalize_citation_text_decodes_literal_json_escapes() -> None:
    raw = '{"content":[{"text":"Algorithm: 8/10\\n\\nCorrectness: 7.5/10"}]}'
    excerpt = "Algorithm: 8/10\n\nCorrectness: 7.5/10"
    assert excerpt in authoritative_source_text(raw)


def test_tool_inner_text_recovers_truncated_pi_envelope() -> None:
    raw = '{"content":[{"text":"Algorithm architecture for normal graybox sets: 8/10\\n\\nCorrectness'
    inner = tool_inner_text(raw)
    assert inner.startswith("Algorithm architecture for normal graybox sets: 8/10")
    assert "{" not in inner[:40]


def test_tool_inner_text_keeps_trailing_truncated_block_after_header() -> None:
    raw = (
        '{"output":[{"text":"Script completed\\n"},'
        '{"text":"Exit code: 0\\nTests 51 passed'
    )
    inner = tool_inner_text(raw)
    assert inner == "Script completed\n\nExit code: 0\nTests 51 passed"


def test_normalize_citation_match_text_collapses_whitespace_and_unicode_quotes() -> None:
    source = "Use actual geometry clearance, not object AABBs:\n\ncandidate valid =\n    camera"
    excerpt = "Use actual geometry clearance, not object AABBs: candidate valid = camera"
    assert normalize_citation_match_text(excerpt) in normalize_citation_match_text(source)
    assert normalize_citation_match_text("object\u2019s box") == normalize_citation_match_text("object's box")


def test_normalize_citation_match_text_strips_ansi_and_decodes_unicode_escapes() -> None:
    source = "\x1b[32m✓\x1b[39m tests/uiFidelity.test.ts"
    excerpt = "\\u001b[32m✓\\u001b[39m tests/uiFidelity.test.ts"
    assert normalize_citation_match_text(excerpt) in normalize_citation_match_text(source)


def test_exact_citation_excerpt_returns_the_stored_source_slice() -> None:
    source = "The Latest Head adds 17 files.\n\n\x1b[32m✓\x1b[39m 51 tests passed."
    assert exact_citation_excerpt(
        "the latest head adds 17 files.",
        source,
    ) == "The Latest Head adds 17 files."
    assert exact_citation_excerpt("✓ 51 tests passed.", source) == (
        "✓\x1b[39m 51 tests passed."
    )


def test_exact_citation_excerpt_does_not_fuzzily_accept_paraphrases() -> None:
    source = "If debug is enabled, return true."
    assert exact_citation_excerpt("if (debug) { ... }", source) is None
    assert exact_citation_excerpt("Debug mode returns the value.", source) is None


def test_freeze_input_hash_matches_projected_events(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    store.create_project("project")
    store.create_thread("thread", "project")
    source = "import { Layer } from './types.js';\n" + ("export type X = number;\n" * 80)
    payload = __import__("json").dumps(
        {"content": source, "role": "tool", "tool_call_id": "call_1"}
    )
    store.append_event(
        project_id="project",
        thread_id="thread",
        event_type="tool_result",
        role="tool",
        content=payload,
    )
    snapshot = freeze_snapshot(store, "thread")
    projected = project_compaction_events(
        [Event.from_row(event) for event in snapshot.events]
    )
    assert snapshot.input_hash == compute_input_hash(
        None, projected, snapshot.snapshot_end_event_id
    )
    assert projected[0].content.startswith("TOOL_READ_STUB")
