import json

from memory_gateway.compaction import Event
from memory_gateway.context import ContextAssembler
from memory_gateway.db import SQLiteStore
from memory_gateway.ingestion import append_novel_messages, parse_stream_message
from memory_gateway.message_views import event_messages, source_text
from memory_gateway.source_projection import project_compaction_events


def store(tmp_path):
    db = SQLiteStore(tmp_path / "memory.db")
    db.ensure_project_and_thread("p", "t")
    return db


def test_full_resends_store_one_event_per_message_and_usage_is_real(tmp_path):
    db = store(tmp_path)
    history = []
    for turn in range(12):
        history.append({"role": "user", "content": f"turn {turn}"})
        result = append_novel_messages(db, project_id="p", thread_id="t", messages=history,
                                      request_id=f"r{turn}", source="request")
        assert len(result.appended_events) == 1
        assert len(result.message_event_ids) == len(history)
        assistant = {"role": "assistant", "content": "same answer"}
        result = append_novel_messages(db, project_id="p", thread_id="t", messages=[assistant],
                                      request_id=f"r{turn}", source="response",
                                      usage={"prompt_tokens": 5000, "completion_tokens": 17})
        assert result.appended_events[0]["token_count"] == 17
        history.append(assistant)
    events = db.list_events("t")
    assert len(events) == 24
    assert [source_text(e) for e in events] == [m["content"] for m in history]
    before = [dict(e) for e in events]
    db.record_request_usage("t", "r11", {"completion_tokens": 17, "api_key": "never-store"})
    assert db.list_events("t") == before
    with db.connect() as c:
        usage = json.loads(c.execute("SELECT usage_json FROM request_usage").fetchone()[0])
    assert usage == {"completion_tokens": 17}


def test_split_sse_tool_arguments_and_empty_wire():
    frames = [
        {"choices": [{"delta": {"role": "assistant", "tool_calls": [
            {"index": 0, "id": "call_x", "function": {"name": "read", "arguments": '{"path":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '"a.py"}'}}]}, "finish_reason": "tool_calls"}]},
    ]
    wire = "".join("data: " + json.dumps(f) + "\n\n" for f in frames).encode() + b"data: [DONE]\n\n"
    message = parse_stream_message([wire[:43], wire[43:67], wire[67:]])
    assert message["tool_calls"][0]["id"] == "call_x"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"path":"a.py"}'
    assert parse_stream_message([b"data: [DONE]\n\n"]) is None


def test_legacy_wire_readable_without_mutating_rows_or_sending_frames(tmp_path):
    db = store(tmp_path)
    request = db.append_event(project_id="p", thread_id="t", event_type="request", role="user",
                              content=json.dumps({"model": "old", "messages": [{"role": "user", "content": "FACT region=east"}]}))
    reply = db.append_event(project_id="p", thread_id="t", event_type="assistant_response", role="assistant",
                            content='data: {"choices":[{"delta":{"content":"ready"}}]}\n\ndata: [DONE]\n\n')
    before = db.list_events("t")
    assert event_messages(request) == [{"role": "user", "content": "FACT region=east"}]
    assert event_messages(reply) == [{"role": "assistant", "content": "ready"}]
    projected = project_compaction_events([Event.from_row(e) for e in before])
    text = "\n".join(e.content for e in projected)
    assert '"model"' not in text and "data:" not in text and "choices" not in text
    text = str(ContextAssembler(db).assemble("t", []).messages)
    assert "data:" not in text and "choices" not in text
    assert db.list_events("t") == before
