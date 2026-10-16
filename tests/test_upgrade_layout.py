from hashlib import sha256

from hypothesis import given, settings, strategies as st

from memory_gateway.context import ContextAssembler
from memory_gateway.db import SQLiteStore, canonical_json
from memory_gateway.ingestion import append_novel_messages


def promote(store, end):
    active = store.get_active_capsule("t")
    text = f"Memory through sequence {end['sequence']}"
    digest = sha256(text.encode()).hexdigest()
    candidate = store.create_capsule(
        thread_id="t", base_capsule_id=active["id"] if active else None, content=text,
        source_event_hash="source", input_hash="input", output_hash=digest, capsule_hash=digest,
        snapshot_start_event_id=end["id"], snapshot_end_event_id=end["id"],
        covered_start_event_id=end["id"], covered_end_event_id=end["id"])
    assert store.mark_capsule_ready(candidate)
    assert store.promote_capsule_cas("t", candidate)


@given(st.lists(st.tuples(st.sampled_from(["user", "assistant"]), st.sampled_from(["repeat", "one", "two"]), st.booleans()), min_size=1, max_size=16))
@settings(max_examples=30, deadline=None)
def test_every_occurrence_is_covered_or_forwarded_and_prefix_stable(sequence):
    from tempfile import TemporaryDirectory
    from pathlib import Path
    with TemporaryDirectory() as tmp:
        store = SQLiteStore(Path(tmp) / "test.db")
        store.ensure_project_and_thread("p", "t")
        assembler = ContextAssembler(store, layout="stable", raw_tail_target_tokens=1, minimum_raw_tail_tokens=1)
        prefix = None
        for index, (role, text, promotion) in enumerate(sequence):
            message = {"role": role, "content": text}
            row = store.append_event(project_id="p", thread_id="t", role=role,
                                     event_type=f"{role}_message", content=canonical_json(message))
            if promotion:
                promote(store, row)
                prefix = None
            instructions = {"role": "system", "content": "unchanged instructions"}
            snapshot = assembler.assemble("t", [instructions, message])
            active = store.get_active_capsule("t")
            boundary = store.get_event(active["covered_end_event_id"])["sequence"] if active else 0
            assert all(e["sequence"] <= boundary or e["id"] in snapshot.raw_tail_event_ids
                       for e in store.list_events("t"))
            expected = [e for e in store.list_events("t") if e["sequence"] > boundary]
            assert len(snapshot.raw_tail_event_ids) == len(expected)
            assert all({"role": e["role"], "content": __import__("json").loads(e["content"])["content"]}
                       in snapshot.messages for e in expected)
            now = canonical_json(snapshot.messages[:2 if active else 1])
            if prefix is not None:
                assert now == prefix
            prefix = now
            assert "RECENT RAW EVENT TAIL" not in canonical_json(snapshot.messages)


def test_capacity_counts_all_uncovered_events_and_tool_schemas(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    for i in range(4):
        append_novel_messages(store, project_id="p", thread_id="t", source="response",
                              request_id=str(i), messages=[{"role": "assistant", "content": "x" * 100}])
    assembler = ContextAssembler(store, layout="stable", raw_tail_target_tokens=1,
                                 foreground_hard_limit_tokens=100)
    snapshot = assembler.assemble("t", [], overhead_tokens=50, reserved_output_tokens=40)
    assert len(snapshot.raw_tail_event_ids) == 4
    assert snapshot.hard_limit_exceeded
    assert sum(snapshot.component_tokens.values()) == snapshot.estimated_tokens


def test_tool_mates_keep_original_protocol_across_coverage(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    assistant = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "read", "arguments": '{"path":"a.py"}'}}]}
    tool = {"role": "tool", "tool_call_id": "c", "content": "file bytes"}
    first = append_novel_messages(store, project_id="p", thread_id="t", messages=[assistant],
                                  source="response", request_id="a").appended_events[0]
    promote(store, first)
    result = append_novel_messages(store, project_id="p", thread_id="t", messages=[assistant, tool],
                                  source="request", request_id="b")
    snapshot = ContextAssembler(store, layout="stable").assemble("t", [assistant, tool], request_event_ids=result.message_event_ids)
    assert snapshot.messages[-2:] == [assistant, tool]
    assert snapshot.substitution.protocol_continuity_messages == 1


def test_actual_message_serialization_is_stable_across_key_order(tmp_path):
    import json
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    assembler = ContextAssembler(store, layout="stable")
    first = assembler.assemble("t", [{"role": "system", "content": "unchanged", "name": "scope"}])
    second = assembler.assemble("t", [{"name": "scope", "content": "unchanged", "role": "system"}])
    assert json.dumps(first.messages) == json.dumps(second.messages)
